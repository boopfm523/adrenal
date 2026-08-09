"""Authentication: password hashing, session issue and verification, login throttling.

Threat model T1. The controls here are deliberately boring and well-trodden:

* Argon2id for password hashing.
* Session cookie carries an opaque random token; only its SHA-256 is stored, so a
  database read yields nothing usable.
* Constant-time comparison everywhere a secret is checked.
* Login throttling with lockout, counted per account.
* A separate CSRF token, checked on every state-changing request.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
import uuid
from datetime import UTC, datetime, timedelta
from typing import Final

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.identity.models import AuthSession, Owner

SESSION_COOKIE_NAME: Final = "hc_session"
CSRF_HEADER_NAME: Final = "X-CSRF-Token"

SESSION_LIFETIME: Final = timedelta(days=14)
SESSION_IDLE_TIMEOUT: Final = timedelta(hours=12)

MAX_FAILED_LOGINS: Final = 5
LOCKOUT_DURATION: Final = timedelta(minutes=15)

_hasher = PasswordHasher()


class AuthenticationError(Exception):
    """Login failed. Deliberately carries no detail about which part failed."""


class AccountLockedError(AuthenticationError):
    """Too many failed attempts."""


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str, password: str) -> bool:
    try:
        return _hasher.verify(password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def authenticate(
    session: Session, email: str, password: str, *, now: datetime | None = None
) -> Owner:
    """Verify credentials and return the owner, or raise.

    The same :class:`AuthenticationError` is raised for an unknown account and a wrong
    password, so the response cannot be used to enumerate accounts.
    """
    now = now or datetime.now(UTC)
    owner = session.scalar(select(Owner).where(Owner.email == email.strip().lower()))

    if owner is None:
        # Spend comparable time on an unknown account so timing does not leak existence.
        _hasher.hash(password)
        raise AuthenticationError("invalid credentials")

    if owner.locked_until is not None and owner.locked_until > now:
        raise AccountLockedError("account temporarily locked")

    if not verify_password(owner.password_hash, password):
        owner.failed_login_count += 1
        if owner.failed_login_count >= MAX_FAILED_LOGINS:
            owner.locked_until = now + LOCKOUT_DURATION
            owner.failed_login_count = 0
        raise AuthenticationError("invalid credentials")

    if needs_rehash(owner.password_hash):
        owner.password_hash = hash_password(password)

    owner.failed_login_count = 0
    owner.locked_until = None
    owner.last_login_at = now
    return owner


def create_session(
    session: Session,
    owner: Owner,
    *,
    user_agent: str | None = None,
    now: datetime | None = None,
) -> tuple[AuthSession, str]:
    """Issue a session. Returns the record and the **plaintext token** for the cookie.

    The plaintext is returned once and never stored; only its hash is persisted.
    """
    now = now or datetime.now(UTC)
    token = secrets.token_urlsafe(32)

    auth_session = AuthSession(
        owner_id=owner.id,
        token_hash=_hash_token(token),
        csrf_token=secrets.token_urlsafe(32),
        created_at=now,
        expires_at=now + SESSION_LIFETIME,
        last_seen_at=now,
        user_agent=(user_agent or "")[:255] or None,
    )
    session.add(auth_session)
    session.flush()
    return auth_session, token


def resolve_session(
    session: Session, token: str, *, now: datetime | None = None
) -> AuthSession | None:
    """Return the live session for a cookie token, or None.

    Expiry, revocation, and idle timeout are all checked here rather than by callers,
    so there is exactly one place that decides whether a session is usable.
    """
    now = now or datetime.now(UTC)
    if not token:
        return None

    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == _hash_token(token))
    )
    if auth_session is None:
        return None
    if auth_session.revoked_at is not None:
        return None
    if auth_session.expires_at <= now:
        return None
    if now - auth_session.last_seen_at > SESSION_IDLE_TIMEOUT:
        auth_session.revoked_at = now
        return None

    auth_session.last_seen_at = now
    return auth_session


def verify_csrf(auth_session: AuthSession, presented: str | None) -> bool:
    if not presented:
        return False
    return hmac.compare_digest(auth_session.csrf_token, presented)


def revoke_session(auth_session: AuthSession, *, now: datetime | None = None) -> None:
    auth_session.revoked_at = now or datetime.now(UTC)


def revoke_all_sessions(
    session: Session, owner_id: uuid.UUID, *, now: datetime | None = None
) -> int:
    """Sign out everywhere. Used after a password change or a suspected compromise."""
    now = now or datetime.now(UTC)
    sessions = session.scalars(
        select(AuthSession).where(
            AuthSession.owner_id == owner_id, AuthSession.revoked_at.is_(None)
        )
    ).all()
    for auth_session in sessions:
        auth_session.revoked_at = now
    return len(sessions)
