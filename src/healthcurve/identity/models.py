"""Owner account and sessions.

Single-owner by design (docs/threat-model.md). Credentials live in their own schema so
they never sit beside health data, and the tables here are the only place a secret is
stored at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, DateTime, ForeignKey, String, UniqueConstraint, func
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import IDENTITY_SCHEMA, IdentityBase, StrEnumType


class Owner(IdentityBase):
    """The single account this installation belongs to."""

    __tablename__ = "owner"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    #: Argon2id hash. The plaintext password is never stored, logged, or exported.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(120))

    #: The *home* zone, and the fallback for any instant before the first
    #: :class:`TimezoneStay`. It is not necessarily where the owner is today --
    #: ``healthcurve.identity.timezones`` is the only supported way to ask that.
    default_timezone: Mapped[str] = mapped_column(String(64), nullable=False, default="UTC")
    locale: Mapped[str] = mapped_column(String(16), nullable=False, default="en-GB")

    __table_args__ = (IDENTITY_SCHEMA,)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Set on a successful login. Used to show "last seen" and to spot unexpected access.
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    failed_login_count: Mapped[int] = mapped_column(nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: TOTP is the supported second factor. The seed is encrypted outside this table;
    #: these fields only enforce policy and prevent a code being replayed.
    mfa_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    mfa_last_totp_step: Mapped[int | None] = mapped_column(BigInteger)


class MfaRecoveryCode(IdentityBase):
    """One high-entropy recovery code, stored only as a SHA-256 digest."""

    __tablename__ = "mfa_recovery_code"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    code_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    used_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("owner_id", "code_hash", name="uq_mfa_recovery_owner_hash"),
        IDENTITY_SCHEMA,
    )


class AuthSession(IdentityBase):
    """A logged-in browser session.

    The cookie carries an opaque token; only its hash is stored, so a database read
    does not yield usable sessions (docs/threat-model.md T1, T3).
    """

    __tablename__ = "auth_session"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )

    token_hash: Mapped[str] = mapped_column(String(128), nullable=False, unique=True)
    csrf_token: Mapped[str] = mapped_column(String(128), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    #: Coarse only -- enough to recognise "that isn't my laptop", never a fingerprint.
    user_agent: Mapped[str | None] = mapped_column(String(255))

    __table_args__ = (IDENTITY_SCHEMA,)

    @property
    def is_active(self) -> bool:
        return self.revoked_at is None


class TimezoneStaySource(StrEnum):
    """How a stay came to be recorded. Nothing here is inferred without confirmation."""

    TELEGRAM = "telegram"
    WEB = "web"
    CLI = "cli"


class TimezoneStay(IdentityBase):
    """The zone the owner was living in, from an instant onwards.

    ``default_timezone`` on :class:`Owner` is a *home* zone, not a current one. It was
    the only zone the application had, so an entry made while travelling was recorded
    and displayed in the home zone -- and a stated wall time ("took it at 8am") was
    converted using the home offset, putting the instant hours from where the dose
    actually happened.

    A stay has no end. It ends when the next one begins, which makes the ledger a step
    function over time and removes the possibility of a gap or an overlap being
    representable at all. An instant *before* the first stay resolves to the home zone,
    so adding this table changes the interpretation of no existing event.
    """

    __tablename__ = "timezone_stay"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )

    #: IANA name. Validated against the tz database before the row is built.
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    #: The instant the owner began experiencing this zone.
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    source: Mapped[TimezoneStaySource] = mapped_column(
        StrEnumType(TimezoneStaySource, 16), nullable=False
    )

    #: Optional free note, e.g. "Denver trip". Never a place name derived from GPS.
    label: Mapped[str | None] = mapped_column(String(120))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "started_at", name="uq_timezone_stay_owner_started_at"),
        IDENTITY_SCHEMA,
    )
