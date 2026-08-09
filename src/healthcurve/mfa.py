"""TOTP MFA orchestration and one-time recovery codes.

The TOTP seed uses the external-key encrypted credential store. Recovery codes have
100 bits of randomness and only SHA-256 digests enter PostgreSQL. Plaintext values are
returned once during enrollment/regeneration and are never logged or recoverable.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Final, Literal

import pyotp
from pydantic import SecretStr
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from healthcurve.config import Settings
from healthcurve.identity.models import MfaRecoveryCode, Owner
from healthcurve.integrations.credentials import (
    CredentialKeyRing,
    delete_credential,
    get_credential,
    set_credential,
)
from healthcurve.operations import audit

PROVIDER: Final = "mfa"
PENDING_SECRET: Final = "totp_pending"  # noqa: S105 -- credential name, not a value
ACTIVE_SECRET: Final = "totp_secret"  # noqa: S105 -- credential name, not a value
RECOVERY_CODE_COUNT: Final = 10
TOTP_INTERVAL_SECONDS: Final = 30
SecondFactorKind = Literal["totp", "recovery"]


class MfaError(RuntimeError):
    pass


class MfaConfigurationError(MfaError):
    pass


class InvalidSecondFactor(MfaError):
    pass


class EnrollmentNotStarted(MfaError):
    pass


@dataclass(frozen=True)
class Enrollment:
    secret: str
    provisioning_uri: str


def _ring(settings: Settings) -> CredentialKeyRing:
    if settings.credential_key_file is None:
        raise MfaConfigurationError("credential key file is required for MFA")
    return CredentialKeyRing.from_file(settings.credential_key_file)


def _secret(
    session: Session, owner_id: uuid.UUID, settings: Settings, name: str
) -> SecretStr | None:
    return get_credential(
        session,
        owner_id=owner_id,
        provider=PROVIDER,
        name=name,
        key_ring=_ring(settings),
    )


def start_enrollment(session: Session, owner: Owner, settings: Settings) -> Enrollment:
    if owner.mfa_enabled:
        raise MfaError("MFA is already enabled")
    secret = pyotp.random_base32(length=32)
    set_credential(
        session,
        owner_id=owner.id,
        provider=PROVIDER,
        name=PENDING_SECRET,
        value=SecretStr(secret),
        key_ring=_ring(settings),
    )
    uri = pyotp.TOTP(secret).provisioning_uri(name=owner.email, issuer_name="HealthCurve")
    return Enrollment(secret, uri)


def _matching_step(secret: str, code: str, now: datetime) -> int | None:
    normalized = code.strip().replace(" ", "")
    if len(normalized) != 6 or not normalized.isdigit():
        return None
    current = int(now.timestamp()) // TOTP_INTERVAL_SECONDS
    totp = pyotp.TOTP(secret, interval=TOTP_INTERVAL_SECONDS)
    for offset in (-1, 0, 1):
        step = current + offset
        if hmac.compare_digest(totp.at(step * TOTP_INTERVAL_SECONDS), normalized):
            return step
    return None


def _new_recovery_codes(session: Session, owner_id: uuid.UUID) -> list[str]:
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.owner_id == owner_id))
    codes: list[str] = []
    for _ in range(RECOVERY_CODE_COUNT):
        compact = base64.b32encode(secrets.token_bytes(13)).decode().rstrip("=")
        display = "-".join(compact[index : index + 5] for index in range(0, len(compact), 5))
        codes.append(display)
        session.add(MfaRecoveryCode(owner_id=owner_id, code_hash=_recovery_hash(display)))
    session.flush()
    return codes


def _recovery_hash(code: str) -> str:
    normalized = code.strip().replace("-", "").replace(" ", "").upper()
    return hashlib.sha256(normalized.encode()).hexdigest()


def confirm_enrollment(
    session: Session,
    owner: Owner,
    settings: Settings,
    code: str,
    *,
    now: datetime | None = None,
) -> list[str]:
    pending = _secret(session, owner.id, settings, PENDING_SECRET)
    if pending is None:
        raise EnrollmentNotStarted("MFA enrollment has not been started")
    measured_at = (now or datetime.now(UTC)).astimezone(UTC)
    step = _matching_step(pending.get_secret_value(), code, measured_at)
    if step is None:
        raise InvalidSecondFactor("invalid authenticator code")
    set_credential(
        session,
        owner_id=owner.id,
        provider=PROVIDER,
        name=ACTIVE_SECRET,
        value=pending,
        key_ring=_ring(settings),
    )
    delete_credential(session, owner_id=owner.id, provider=PROVIDER, name=PENDING_SECRET)
    recovery_codes = _new_recovery_codes(session, owner.id)
    owner.mfa_enabled = True
    owner.mfa_last_totp_step = step
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.MFA_ENROLLED,
        target_type="owner_mfa",
        target_id=owner.id,
    )
    return recovery_codes


def verify_second_factor(
    session: Session,
    owner: Owner,
    settings: Settings,
    code: str,
    *,
    now: datetime | None = None,
) -> SecondFactorKind:
    if not owner.mfa_enabled:
        raise InvalidSecondFactor("MFA is not enabled")
    session.refresh(owner, with_for_update=True)
    measured_at = (now or datetime.now(UTC)).astimezone(UTC)
    secret = _secret(session, owner.id, settings, ACTIVE_SECRET)
    if secret is None:
        raise MfaConfigurationError("MFA seed is unavailable")
    step = _matching_step(secret.get_secret_value(), code, measured_at)
    if step is not None:
        if owner.mfa_last_totp_step is not None and step <= owner.mfa_last_totp_step:
            raise InvalidSecondFactor("authenticator code was already used")
        owner.mfa_last_totp_step = step
        return "totp"

    row = session.scalar(
        select(MfaRecoveryCode)
        .where(
            MfaRecoveryCode.owner_id == owner.id,
            MfaRecoveryCode.code_hash == _recovery_hash(code),
            MfaRecoveryCode.used_at.is_(None),
        )
        .with_for_update()
    )
    if row is None:
        raise InvalidSecondFactor("invalid second factor")
    row.used_at = measured_at
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.MFA_RECOVERY_CODE_USED,
        target_type="mfa_recovery_code",
        target_id=row.id,
    )
    return "recovery"


def regenerate_recovery_codes(
    session: Session, owner: Owner, *, action: audit.AuditAction
) -> list[str]:
    codes = _new_recovery_codes(session, owner.id)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=action,
        target_type="owner_mfa",
        target_id=owner.id,
    )
    return codes


def remove(session: Session, owner: Owner) -> None:
    delete_credential(session, owner_id=owner.id, provider=PROVIDER, name=ACTIVE_SECRET)
    delete_credential(session, owner_id=owner.id, provider=PROVIDER, name=PENDING_SECRET)
    session.execute(delete(MfaRecoveryCode).where(MfaRecoveryCode.owner_id == owner.id))
    owner.mfa_enabled = False
    owner.mfa_last_totp_step = None
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.MFA_REMOVED,
        target_type="owner_mfa",
        target_id=owner.id,
    )


def recovery_codes_remaining(session: Session, owner_id: uuid.UUID) -> int:
    return int(
        session.scalar(
            select(func.count())
            .select_from(MfaRecoveryCode)
            .where(MfaRecoveryCode.owner_id == owner_id, MfaRecoveryCode.used_at.is_(None))
        )
        or 0
    )
