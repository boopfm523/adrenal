"""The canonical event base: shared time, source, provenance, and correction fields.

Every recorded fact in HealthCurve -- a dose, a symptom, an injection, an imported
sleep session -- carries this set. It is a mixin rather than a single table so each
event type keeps its own columns and constraints, while the fields that make a record
*trustworthy* are impossible to omit.

The fields come from plan section 6 and are required non-null by SAFE-09. There is no
nullable escape hatch: an event whose source or timezone is unknown is not a record
worth keeping.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, ClassVar

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    SmallInteger,
    String,
    Uuid,
    func,
    text,
)
from sqlalchemy.orm import Mapped, declarative_mixin, declared_attr, mapped_column

from healthcurve.db import FACT_SCHEMA, StrEnumType
from healthcurve.events.timekeeping import EventTime


def event_table_args(tablename: str) -> tuple[Any, ...]:
    """Constraints and indexes every event table must carry.

    A function rather than a mixin ``__table_args__`` because SQLAlchemy gives a
    subclass no way to *extend* an inherited ``__table_args__`` -- it can only replace
    it. Composing explicitly means a model that adds its own constraint cannot silently
    drop the shared ones:

        __table_args__ = (my_constraint, *event_table_args("symptom_event"))
    """
    return (
        # A record cannot correct itself.
        CheckConstraint("supersedes_id IS NULL OR supersedes_id <> id", name="no_self_supersede"),
        # An event cannot be known before it happened.
        CheckConstraint("recorded_at >= occurred_at", name="recorded_after_occurred"),
        # Real-world offsets span UTC-12:00 to UTC+14:00.
        CheckConstraint("utc_offset_minutes BETWEEN -720 AND 840", name="offset_within_real_range"),
        # A row may be superseded at most once, so the correction chain stays linear and
        # "the current version" is never ambiguous.
        Index(
            f"uq_{tablename}_supersedes_once",
            "supersedes_id",
            unique=True,
            postgresql_where=text("supersedes_id IS NOT NULL"),
        ),
        # Provider imports are idempotent by (source, provider id, revision).
        Index(
            f"uq_{tablename}_provider_identity",
            "source_type",
            "provider_id",
            "source_revision",
            unique=True,
            postgresql_where=text("provider_id IS NOT NULL"),
        ),
        Index(f"ix_{tablename}_occurred_at", "occurred_at"),
        # Every event table is a recorded fact (SAFE-01).
        FACT_SCHEMA,
    )


class SourceType(StrEnum):
    """How a record entered the system. Never inferred (SAFE-14)."""

    WEB = "web"  # typed into the web application by the owner
    TELEGRAM = "telegram"  # confirmed from a Telegram capture draft
    CSV_IMPORT = "csv_import"  # reviewed spreadsheet import
    PROVIDER = "provider"  # pulled from an integration (Garmin, weather)
    MIGRATION = "migration"  # created by a data migration, with provenance


class ConfirmationState(StrEnum):
    """Why this record is trusted. Immutable except through a correction (SAFE-14)."""

    #: The owner entered it directly. No extraction was involved.
    DIRECT = "direct"
    #: The owner reviewed and confirmed an extraction draft (SAFE-11).
    CONFIRMED_FROM_DRAFT = "confirmed_from_draft"
    #: Imported from a trusted provider; the provider is the authority.
    PROVIDER_IMPORTED = "provider_imported"


@declarative_mixin
class EventMixin:
    """Shared columns for every recorded fact.

    Mixed into models on ``FactBase`` so they land in the ``fact`` schema (SAFE-01).
    """

    #: Each event type declares its own. Annotated (not assigned) so a subclass that
    #: forgets it fails the type check rather than at import time.
    __tablename__: ClassVar[str]

    id: Mapped[uuid.UUID] = mapped_column(
        Uuid, primary_key=True, default=uuid.uuid4, doc="Stable identity across corrections."
    )

    #: Plan section 6 lists owner among the shared event fields. Single-owner today,
    #: but every query is owner-scoped so the boundary exists from the start rather
    #: than being retrofitted across every table later.
    owner_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("identity.owner.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    # --- Time (SAFE-09). See healthcurve.events.timekeeping for why all four. ---
    occurred_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        doc="The instant the event happened, in UTC. The only safe basis for comparison.",
    )
    local_time: Mapped[datetime] = mapped_column(
        DateTime(timezone=False),
        nullable=False,
        doc="Wall-clock time as the owner experienced it. Naive by design.",
    )
    timezone: Mapped[str] = mapped_column(
        String(64), nullable=False, doc="IANA zone name at the time of the event."
    )
    utc_offset_minutes: Mapped[int] = mapped_column(
        SmallInteger,
        nullable=False,
        doc="Offset as it actually was, pinned so later tz-rule changes cannot shift history.",
    )

    # --- Capture and source ---
    recorded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        doc="When HealthCurve learned of the event, which is not when it happened.",
    )
    source_type: Mapped[SourceType] = mapped_column(StrEnumType(SourceType, 32), nullable=False)
    provider_id: Mapped[str | None] = mapped_column(
        String(255), doc="The provider's own identifier, for idempotent import."
    )
    source_revision: Mapped[str | None] = mapped_column(
        String(128), doc="Provider revision or checksum; lets a revised import reconcile."
    )
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, doc="The import that produced this row, for bulk review and rollback."
    )

    # --- Trust ---
    confirmation_state: Mapped[ConfirmationState] = mapped_column(
        StrEnumType(ConfirmationState, 32), nullable=False
    )

    # --- Correction lineage (SAFE-08) ---
    @declared_attr
    @classmethod
    def supersedes_id(cls) -> Mapped[uuid.UUID | None]:
        """The row this one corrects. The superseded row is retained, never rewritten."""
        return mapped_column(
            Uuid,
            ForeignKey(f"fact.{cls.__tablename__}.id", ondelete="RESTRICT"),
            nullable=True,
        )

    correction_reason: Mapped[str | None] = mapped_column(String(500))

    notes: Mapped[str | None] = mapped_column(String(2000))

    # -----------------------------------------------------------------------
    # Helpers
    # -----------------------------------------------------------------------

    @property
    def event_time(self) -> EventTime:
        return EventTime(
            occurred_at=self.occurred_at.astimezone(UTC),
            local_time=self.local_time,
            timezone=self.timezone,
            utc_offset_minutes=self.utc_offset_minutes,
        )

    def apply_event_time(self, event_time: EventTime) -> None:
        """Set all four time fields together, so they cannot drift apart."""
        self.occurred_at = event_time.occurred_at
        self.local_time = event_time.local_time
        self.timezone = event_time.timezone
        self.utc_offset_minutes = event_time.utc_offset_minutes

    @property
    def is_correction(self) -> bool:
        return self.supersedes_id is not None
