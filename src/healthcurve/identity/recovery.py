"""Development-only recovery for an inaccessible bootstrap owner account.

This is deliberately not a general password-reset mechanism.  It exists only for a
trusted local operator during development, before MFA is enrolled.  Production and
MFA-bearing accounts fail closed.
"""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import EmailStr, TypeAdapter, ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from healthcurve.config import Environment
from healthcurve.identity import service as auth
from healthcurve.identity.models import MfaRecoveryCode, Owner
from healthcurve.operations import audit

_EMAIL = TypeAdapter(EmailStr)
MINIMUM_PASSWORD_LENGTH = 12


class OwnerRecoveryError(ValueError):
    """The requested recovery is unsafe or invalid."""


def normalise_email(value: str) -> str:
    """Validate and normalize an owner email without exposing it in errors."""
    try:
        return str(_EMAIL.validate_python(value.strip())).lower()
    except ValidationError as exc:
        raise OwnerRecoveryError("Enter a valid email address.") from exc


def recover_owner_access(
    session: Session,
    owner: Owner,
    *,
    environment: Environment,
    new_email: str,
    new_password: str,
    now: datetime | None = None,
) -> int:
    """Replace development credentials in-place and revoke every live session.

    No health, plan, AI, document, or integration row is selected or modified.  The
    caller owns the transaction, so identity mutation, revocation, and audit are
    committed or rolled back together.
    """
    if environment is not Environment.DEV:
        raise OwnerRecoveryError("Owner access recovery is available only in development.")

    recovery_code_count = session.scalar(
        select(func.count())
        .select_from(MfaRecoveryCode)
        .where(MfaRecoveryCode.owner_id == owner.id)
    )
    if owner.mfa_enabled or bool(recovery_code_count):
        raise OwnerRecoveryError(
            "Owner access recovery is disabled after MFA enrollment. Use an MFA recovery code."
        )

    if len(new_password) < MINIMUM_PASSWORD_LENGTH:
        raise OwnerRecoveryError(f"Password must be at least {MINIMUM_PASSWORD_LENGTH} characters.")

    email = normalise_email(new_email)
    password_hash = auth.hash_password(new_password)
    recovery_time = now or datetime.now(UTC)

    owner.email = email
    owner.password_hash = password_hash
    owner.failed_login_count = 0
    owner.locked_until = None
    revoked = auth.revoke_all_sessions(session, owner.id, now=recovery_time)

    audit.record(
        session,
        actor="system",
        action=audit.AuditAction.OWNER_ACCESS_RECOVERED,
        target_type="owner",
        target_id=owner.id,
        change_summary=f"email,password; sessions_revoked={revoked}; environment=dev",
    )
    return revoked
