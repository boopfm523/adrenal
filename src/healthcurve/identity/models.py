"""Owner account and sessions.

Single-owner by design (docs/threat-model.md). Credentials live in their own schema so
they never sit beside health data, and the tables here are the only place a secret is
stored at all.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import IDENTITY_SCHEMA, IdentityBase


class Owner(IdentityBase):
    """The single account this installation belongs to."""

    __tablename__ = "owner"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)

    #: Argon2id hash. The plaintext password is never stored, logged, or exported.
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)

    display_name: Mapped[str | None] = mapped_column(String(120))
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
