"""Telegram update deduplication.

Telegram retries an update until it receives a 200. Without a record of what has been
seen, a retry would create a second draft from one message (threat model T4, replay).

Only the update ID and outcome are stored -- never the message text, which lives on
the draft and is purged when the draft resolves.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import OPS_SCHEMA, OpsBase, StrEnumType


class TelegramUpdate(OpsBase):
    __tablename__ = "telegram_update"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    #: Telegram's own monotonic update id. Unique, so a replay is a no-op.
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)
    provider_message_id: Mapped[int | None] = mapped_column(BigInteger)
    provider_sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reference_time_source: Mapped[str] = mapped_column(
        String(32), nullable=False, default="processing_time_fallback"
    )

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: processed | rejected_chat | rejected_secret | error | ignored
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    draft_id: Mapped[uuid.UUID | None] = mapped_column()

    __table_args__ = (OPS_SCHEMA,)


class DoseReminderState(StrEnum):
    PENDING = "pending"
    SENT = "sent"
    SNOOZED = "snoozed"
    DISMISSED = "dismissed"
    RECORD_PENDING = "record_pending"
    SATISFIED = "satisfied"


class TelegramDoseReminder(OpsBase):
    """Durable, content-minimal reminder state for one plan slot occurrence."""

    __tablename__ = "telegram_dose_reminder"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    regimen_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan.regimen_version.id", ondelete="CASCADE"), nullable=False
    )
    slot_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan.regimen_dose_slot.id", ondelete="CASCADE"), nullable=False
    )
    local_date: Mapped[date] = mapped_column(Date, nullable=False)
    scheduled_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    due_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, index=True)
    state: Mapped[DoseReminderState] = mapped_column(
        StrEnumType(DoseReminderState, 24), nullable=False, default=DoseReminderState.PENDING
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    sent_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    # Deliberately not an FK into the AI namespace: deleting generated content must
    # never cascade into durable operational state (SAFE-06).
    draft_id: Mapped[uuid.UUID | None] = mapped_column()

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "slot_id", "local_date", name="uq_telegram_dose_reminder_occurrence"
        ),
        OPS_SCHEMA,
    )


class LocationRequestState(StrEnum):
    PENDING = "pending"
    ATTACHED = "attached"
    USED = "used"
    CANCELLED = "cancelled"
    EXPIRED = "expired"


class TelegramLocationRequest(OpsBase):
    """Correlation state containing rounded coordinates only—never raw phone GPS."""

    __tablename__ = "telegram_location_request"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    # Deliberately not an FK into ai: deleting AI drafts must remain independent.
    draft_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    state: Mapped[LocationRequestState] = mapped_column(
        StrEnumType(LocationRequestState, 16), nullable=False
    )
    rounded_latitude: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    rounded_longitude: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    location_label: Mapped[str | None] = mapped_column(String(120))
    requested_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "(rounded_latitude IS NULL) = (rounded_longitude IS NULL)",
            name="coordinate_pair",
        ),
        CheckConstraint(
            "rounded_latitude IS NULL OR rounded_latitude BETWEEN -90 AND 90",
            name="latitude_range",
        ),
        CheckConstraint(
            "rounded_longitude IS NULL OR rounded_longitude BETWEEN -180 AND 180",
            name="longitude_range",
        ),
        CheckConstraint(
            "rounded_latitude IS NULL OR (rounded_latitude = round(rounded_latitude, 1) "
            "AND rounded_longitude = round(rounded_longitude, 1))",
            name="coordinates_rounded",
        ),
        Index(
            "uq_telegram_location_request_pending_owner",
            "owner_id",
            unique=True,
            postgresql_where=text("state IN ('pending', 'attached')"),
        ),
        OPS_SCHEMA,
    )
