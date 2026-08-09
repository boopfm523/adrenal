"""Encrypted class-C8 integration credentials.

Ciphertext lives in PostgreSQL; AES keys live only in an owner-controlled key file.
A database dump therefore contains no usable provider credential.  The authenticated
encryption associated data binds every ciphertext to its row, owner, provider and
name, so copied or relabelled ciphertext will not decrypt.

The key file is deliberately a small, versioned key ring rather than one environment
variable.  Keeping old keys while making a new key active allows every row to be
re-encrypted transactionally before an old key is removed.
"""

from __future__ import annotations

import base64
import json
import os
import re
import secrets
import stat
import tempfile
import uuid
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from pydantic import SecretStr
from sqlalchemy import CheckConstraint, DateTime, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, Session, mapped_column
from sqlalchemy.sql import func, select

from healthcurve.db import IDENTITY_SCHEMA, IdentityBase
from healthcurve.operations import audit

KEY_FILE_VERSION: Final = 1
CIPHER_VERSION: Final = 1
NONCE_BYTES: Final = 12
KEY_BYTES: Final = 32
_LABEL = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


class CredentialError(RuntimeError):
    """Safe, non-secret-bearing credential-store failure."""


class CredentialConfigurationError(CredentialError):
    """The external key ring is absent, unsafe, or malformed."""


class CredentialDecryptionError(CredentialError):
    """Ciphertext cannot be authenticated with its named key."""


class IntegrationCredential(IdentityBase):
    """An encrypted provider secret. Plaintext is never assigned to this model."""

    __tablename__ = "integration_credential"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    provider: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    key_id: Mapped[str] = mapped_column(String(64), nullable=False)
    cipher_version: Mapped[int] = mapped_column(nullable=False, default=CIPHER_VERSION)
    nonce: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "provider", "name", name="uq_credential_owner_provider_name"),
        CheckConstraint(f"octet_length(nonce) = {NONCE_BYTES}", name="credential_nonce_length"),
        CheckConstraint("octet_length(ciphertext) >= 16", name="credential_ciphertext_has_tag"),
        IDENTITY_SCHEMA,
    )


@dataclass(frozen=True)
class CredentialKeyRing:
    """Validated external encryption keys; repr intentionally hides key bytes."""

    active_key_id: str
    _keys: dict[str, bytes] = field(repr=False)

    @classmethod
    def from_file(cls, path: Path) -> CredentialKeyRing:
        try:
            raw: Any = json.loads(_read_key_file(path))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise CredentialConfigurationError("credential_key_file_unreadable") from exc
        if not isinstance(raw, dict) or raw.get("version") != KEY_FILE_VERSION:
            raise CredentialConfigurationError("credential_key_file_version_invalid")
        active = raw.get("active_key_id")
        encoded_keys = raw.get("keys")
        if not isinstance(active, str) or not _LABEL.fullmatch(active):
            raise CredentialConfigurationError("credential_active_key_id_invalid")
        if not isinstance(encoded_keys, dict) or not encoded_keys:
            raise CredentialConfigurationError("credential_keys_missing")

        keys: dict[str, bytes] = {}
        for key_id, encoded in encoded_keys.items():
            if not isinstance(key_id, str) or not _LABEL.fullmatch(key_id):
                raise CredentialConfigurationError("credential_key_id_invalid")
            if not isinstance(encoded, str):
                raise CredentialConfigurationError("credential_key_encoding_invalid")
            try:
                key = base64.b64decode(encoded, altchars=b"-_", validate=True)
            except ValueError as exc:
                raise CredentialConfigurationError("credential_key_encoding_invalid") from exc
            if len(key) != KEY_BYTES:
                raise CredentialConfigurationError("credential_key_length_invalid")
            keys[key_id] = key
        if active not in keys:
            raise CredentialConfigurationError("credential_active_key_missing")
        return cls(active_key_id=active, _keys=keys)

    def key(self, key_id: str) -> bytes:
        try:
            return self._keys[key_id]
        except KeyError as exc:
            raise CredentialDecryptionError("credential_key_unavailable") from exc

    @property
    def key_ids(self) -> frozenset[str]:
        """Non-secret identifiers present in the ring."""
        return frozenset(self._keys)


def create_key_file(path: Path, key_id: str) -> None:
    """Create a new owner-only key ring without ever overwriting an existing one."""
    _validate_label(key_id, "key_id")
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = _key_file_payload(key_id, {key_id: secrets.token_bytes(KEY_BYTES)})
    try:
        descriptor = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    except FileExistsError as exc:
        raise CredentialConfigurationError("credential_key_file_exists") from exc
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
    except Exception:
        path.unlink(missing_ok=True)
        raise


def read_private_secret_file(path: Path) -> SecretStr:
    """Read an optional non-interactive value with the same strict file controls."""
    value = _read_key_file(path).rstrip("\r\n")
    if not value:
        raise CredentialConfigurationError("credential_value_empty")
    return SecretStr(value)


def add_active_key(path: Path, key_id: str) -> None:
    """Add a new active key atomically while retaining every decryption key."""
    _validate_label(key_id, "key_id")
    ring = CredentialKeyRing.from_file(path)
    if key_id in ring.key_ids:
        raise CredentialConfigurationError("credential_key_id_exists")
    keys = {existing: ring.key(existing) for existing in ring.key_ids}
    keys[key_id] = secrets.token_bytes(KEY_BYTES)
    _replace_key_file(path, _key_file_payload(key_id, keys))


def retire_key(path: Path, key_id: str) -> None:
    """Remove an inactive key after the caller proved no database row names it."""
    _validate_label(key_id, "key_id")
    ring = CredentialKeyRing.from_file(path)
    if key_id == ring.active_key_id:
        raise CredentialConfigurationError("credential_active_key_cannot_retire")
    if key_id not in ring.key_ids:
        raise CredentialConfigurationError("credential_key_unavailable")
    keys = {existing: ring.key(existing) for existing in ring.key_ids if existing != key_id}
    _replace_key_file(path, _key_file_payload(ring.active_key_id, keys))


def _replace_key_file(path: Path, payload: dict[str, Any]) -> None:
    """Atomically replace an already validated key file with owner-only permissions."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=".credential-keys-", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        os.fchmod(descriptor, 0o600)
        with os.fdopen(descriptor, "w", encoding="utf-8") as stream:
            json.dump(payload, stream, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def set_credential(
    session: Session,
    *,
    owner_id: uuid.UUID,
    provider: str,
    name: str,
    value: SecretStr,
    key_ring: CredentialKeyRing,
) -> IntegrationCredential:
    """Create or replace one credential; plaintext never enters a mapped field."""
    _validate_label(provider, "provider")
    _validate_label(name, "name")
    row = session.scalar(
        select(IntegrationCredential)
        .where(
            IntegrationCredential.owner_id == owner_id,
            IntegrationCredential.provider == provider,
            IntegrationCredential.name == name,
        )
        .with_for_update()
    )
    created = row is None
    if row is None:
        # The UUID is part of the authenticated associated data, so it must exist
        # before encryption rather than relying on the insert-time column default.
        row = IntegrationCredential(
            id=uuid.uuid4(), owner_id=owner_id, provider=provider, name=name
        )
        session.add(row)

    _encrypt_into(row, value.get_secret_value(), key_ring)
    session.flush()
    if created:
        audit.record(
            session,
            actor=audit.actor_for_owner(owner_id),
            action=audit.AuditAction.INTEGRATION_CONNECTED,
            target_type="integration_credential",
            target_id=row.id,
            change_summary=f"provider={provider};name={name}",
        )
    else:
        audit.record(
            session,
            actor=audit.actor_for_owner(owner_id),
            action=audit.AuditAction.INTEGRATION_CREDENTIAL_UPDATED,
            target_type="integration_credential",
            target_id=row.id,
            change_summary=f"provider={provider};name={name}",
        )
    return row


def get_credential(
    session: Session,
    *,
    owner_id: uuid.UUID,
    provider: str,
    name: str,
    key_ring: CredentialKeyRing,
) -> SecretStr | None:
    _validate_label(provider, "provider")
    _validate_label(name, "name")
    row = session.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.owner_id == owner_id,
            IntegrationCredential.provider == provider,
            IntegrationCredential.name == name,
        )
    )
    return None if row is None else SecretStr(_decrypt(row, key_ring))


def delete_credential(session: Session, *, owner_id: uuid.UUID, provider: str, name: str) -> bool:
    row = session.scalar(
        select(IntegrationCredential)
        .where(
            IntegrationCredential.owner_id == owner_id,
            IntegrationCredential.provider == provider,
            IntegrationCredential.name == name,
        )
        .with_for_update()
    )
    if row is None:
        return False
    identifier = row.id
    session.delete(row)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.INTEGRATION_DISCONNECTED,
        target_type="integration_credential",
        target_id=identifier,
        change_summary=f"provider={provider};name={name}",
    )
    return True


def rotate_credentials(
    session: Session, *, key_ring: CredentialKeyRing, owner_id: uuid.UUID | None = None
) -> int:
    """Re-encrypt rows with the active key in the caller's single transaction."""
    statement = select(IntegrationCredential).with_for_update()
    if owner_id is not None:
        statement = statement.where(IntegrationCredential.owner_id == owner_id)
    rotated = 0
    for row in session.scalars(statement):
        if row.key_id == key_ring.active_key_id:
            continue
        plaintext = _decrypt(row, key_ring)
        _encrypt_into(row, plaintext, key_ring)
        audit.record(
            session,
            actor=audit.actor_for_owner(row.owner_id),
            action=audit.AuditAction.INTEGRATION_CREDENTIAL_ROTATED,
            target_type="integration_credential",
            target_id=row.id,
            change_summary=f"provider={row.provider};name={row.name};key_id={row.key_id}",
        )
        rotated += 1
    session.flush()
    return rotated


def _encrypt_into(row: IntegrationCredential, plaintext: str, key_ring: CredentialKeyRing) -> None:
    nonce = secrets.token_bytes(NONCE_BYTES)
    row.key_id = key_ring.active_key_id
    row.cipher_version = CIPHER_VERSION
    row.nonce = nonce
    row.ciphertext = AESGCM(key_ring.key(row.key_id)).encrypt(
        nonce, plaintext.encode("utf-8"), _associated_data(row)
    )


def _decrypt(row: IntegrationCredential, key_ring: CredentialKeyRing) -> str:
    if row.cipher_version != CIPHER_VERSION:
        raise CredentialDecryptionError("credential_cipher_version_unsupported")
    try:
        plaintext = AESGCM(key_ring.key(row.key_id)).decrypt(
            row.nonce, row.ciphertext, _associated_data(row)
        )
        return plaintext.decode("utf-8")
    except (InvalidTag, UnicodeDecodeError) as exc:
        raise CredentialDecryptionError("credential_authentication_failed") from exc


def _associated_data(row: IntegrationCredential) -> bytes:
    return (
        f"healthcurve:c8:v{row.cipher_version}:{row.id}:{row.owner_id}:{row.provider}:{row.name}"
    ).encode()


def _read_key_file(path: Path) -> str:
    """Read through one no-follow descriptor, closing the lstat/open race."""
    try:
        descriptor = os.open(path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0))
    except OSError as exc:
        raise CredentialConfigurationError("credential_key_file_unavailable") from exc
    try:
        details = os.fstat(descriptor)
        if not stat.S_ISREG(details.st_mode):
            raise CredentialConfigurationError("credential_key_file_not_regular")
        if details.st_mode & 0o077:
            raise CredentialConfigurationError("credential_key_file_permissions_unsafe")
        with os.fdopen(descriptor, encoding="utf-8") as stream:
            descriptor = -1
            return stream.read()
    finally:
        if descriptor >= 0:
            os.close(descriptor)


def _validate_label(value: str, field_name: str) -> None:
    if not _LABEL.fullmatch(value):
        raise CredentialConfigurationError(f"credential_{field_name}_invalid")


def _key_file_payload(active_key_id: str, keys: dict[str, bytes]) -> dict[str, Any]:
    return {
        "version": KEY_FILE_VERSION,
        "active_key_id": active_key_id,
        "keys": {
            key_id: base64.urlsafe_b64encode(value).decode("ascii")
            for key_id, value in keys.items()
        },
    }
