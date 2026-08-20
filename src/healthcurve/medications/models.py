"""Medications, physician-approved regimens, and actual doses.

The central rule of this module is that **a plan and a dose are different kinds of
thing** (SAFE-03). ``RegimenDoseSlot`` says what should happen; ``DoseEvent`` says what
did. They live in different schemas, and no operation converts one into the other.

Amounts are ``NUMERIC(10,4)`` with an explicit unit column. Binary floats are
prohibited for clinical quantities (ADR-0001): 15.0 mg must never become 14.999999.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    SmallInteger,
    String,
    Text,
    Time,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import TSRANGE, ExcludeConstraint, Range
from sqlalchemy.orm import Mapped, mapped_column, relationship

from healthcurve.db import PLAN_SCHEMA, FactBase, PlanBase, StrEnumType
from healthcurve.events.base import EventMixin, event_table_args

#: One place for the amount type, so no table can quietly use a float.
AmountType = Numeric(10, 4)


class DoseUnit(StrEnum):
    MG = "mg"
    MCG = "mcg"
    ML = "ml"
    TABLET = "tablet"


class Route(StrEnum):
    ORAL = "oral"
    INTRAMUSCULAR = "intramuscular"
    SUBCUTANEOUS = "subcutaneous"
    INTRAVENOUS = "intravenous"


class RegimenStatus(StrEnum):
    """Only a human can move a version to ``APPROVED`` (SAFE-16)."""

    DRAFT = "draft"
    APPROVED = "approved"
    RETIRED = "retired"


class DoseTimingMode(StrEnum):
    """How a physician-plan slot is anchored within the local day."""

    FIXED_TIME = "fixed_time"
    WAKE = "wake"


class DoseCategory(StrEnum):
    """Why this dose was taken. Plan section 3."""

    SCHEDULED = "scheduled"
    LATE = "late"
    REPLACEMENT = "replacement"
    STRESS = "stress"
    TAPER = "taper"
    EMERGENCY = "emergency"


class InstructionCategory(StrEnum):
    ILLNESS = "illness"
    PROCEDURE = "procedure"
    EXERCISE = "exercise"
    EMERGENCY = "emergency"
    GENERAL = "general"


# ---------------------------------------------------------------------------
# plan schema
# ---------------------------------------------------------------------------


class Medication(PlanBase):
    """A medication the owner takes or has taken.

    In the ``plan`` namespace because it is the vocabulary the plan is written in.
    Letting AI create a medication would let it invent a dose by the back door.
    """

    __tablename__ = "medication"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    name: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Lowercased name used for matching extraction output; never shown to the user.
    normalized_name: Mapped[str] = mapped_column(String(200), nullable=False)
    formulation: Mapped[str | None] = mapped_column(String(120))
    strength: Mapped[Decimal | None] = mapped_column(AmountType)
    strength_unit: Mapped[str | None] = mapped_column(String(16))
    default_unit: Mapped[DoseUnit] = mapped_column(StrEnumType(DoseUnit, 16), nullable=False)
    default_route: Mapped[Route] = mapped_column(StrEnumType(Route, 24), nullable=False)

    active_from: Mapped[date | None] = mapped_column(Date)
    active_to: Mapped[date | None] = mapped_column(Date)
    notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        UniqueConstraint("owner_id", "normalized_name", "strength", name="unique_per_owner"),
        CheckConstraint("strength IS NULL OR strength > 0", name="strength_positive"),
        CheckConstraint(
            "active_to IS NULL OR active_from IS NULL OR active_to >= active_from",
            name="active_range_ordered",
        ),
        PLAN_SCHEMA,
    )


class RegimenVersion(PlanBase):
    """An immutable version of the physician-approved medication schedule.

    Versions are never edited in place. A change means a new version with its own
    effective interval, so any past date can still be answered correctly.

    The exclusion constraint is the important part: two *approved* versions for the
    same owner cannot overlap in time. That is enforced by PostgreSQL rather than by
    application code, so a bug or a manual SQL fix cannot produce a period where the
    system cannot say which plan was in force.
    """

    __tablename__ = "regimen_version"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    version_label: Mapped[str] = mapped_column(String(60), nullable=False)
    status: Mapped[RegimenStatus] = mapped_column(
        StrEnumType(RegimenStatus, 16), nullable=False, default=RegimenStatus.DRAFT
    )

    #: Half-open [from, to). ``effective_to`` NULL means "still in force".
    #: Canonical UTC instants are stored naive for PostgreSQL ``tsrange`` compatibility.
    effective_from: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    effective_to: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    effective_period: Mapped[Range[datetime]] = mapped_column(TSRANGE, nullable=False)
    #: Capture-time wall-clock provenance. Legacy rows deliberately leave these NULL
    #: because their original timezone cannot be reconstructed honestly.
    effective_timezone: Mapped[str | None] = mapped_column(String(64))
    effective_from_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    effective_to_local: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    effective_from_utc_offset_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    effective_to_utc_offset_minutes: Mapped[int | None] = mapped_column(SmallInteger)
    effective_time_provenance: Mapped[str] = mapped_column(
        String(32), nullable=False, default="explicit_timezone"
    )

    # --- Approval provenance. AI can never write any of this (SAFE-16). ---
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    approved_by: Mapped[str | None] = mapped_column(
        String(200), doc="Clinician name or role, as recorded by the owner."
    )
    approval_source: Mapped[str | None] = mapped_column(
        String(200), doc="Where the approval came from: letter, consultation, portal message."
    )
    source_document_checksum: Mapped[str | None] = mapped_column(String(128))

    retired_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    slots: Mapped[list[RegimenDoseSlot]] = relationship(
        back_populates="regimen_version", cascade="all, delete-orphan", lazy="selectin"
    )
    instructions: Mapped[list[ApprovedInstruction]] = relationship(
        back_populates="regimen_version", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        # No two approved versions may overlap for one owner.
        ExcludeConstraint(
            ("owner_id", "="),
            ("effective_period", "&&"),
            name="approved_versions_do_not_overlap",
            using="gist",
            where=text("status = 'approved'"),
        ),
        CheckConstraint(
            "effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from",
            name="effective_range_ordered",
        ),
        CheckConstraint(
            "effective_from_utc_offset_minutes IS NULL OR "
            "effective_from_utc_offset_minutes BETWEEN -720 AND 840",
            name="effective_from_offset_within_real_range",
        ),
        CheckConstraint(
            "effective_to_utc_offset_minutes IS NULL OR "
            "effective_to_utc_offset_minutes BETWEEN -720 AND 840",
            name="effective_to_offset_within_real_range",
        ),
        # Approved status requires provenance. An approval with no approver is not an
        # approval, and SAFE-16 depends on that being unforgeable.
        CheckConstraint(
            "status <> 'approved' OR (approved_at IS NOT NULL AND approved_by IS NOT NULL "
            "AND effective_from IS NOT NULL)",
            name="approved_requires_provenance",
        ),
        CheckConstraint(
            "status <> 'retired' OR retired_at IS NOT NULL",
            name="retired_requires_timestamp",
        ),
        Index("ix_regimen_version_owner_status", "owner_id", "status"),
        PLAN_SCHEMA,
    )


class RegimenDoseSlot(PlanBase):
    """A planned dose within a regimen version. What *should* happen."""

    __tablename__ = "regimen_dose_slot"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    regimen_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan.regimen_version.id", ondelete="CASCADE"), nullable=False, index=True
    )
    medication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan.medication.id", ondelete="RESTRICT"), nullable=False
    )

    timing_mode: Mapped[DoseTimingMode] = mapped_column(
        StrEnumType(DoseTimingMode, 16), nullable=False, default=DoseTimingMode.FIXED_TIME
    )
    #: Local wall time, not an instant: "07:00" means 7am wherever the owner is.
    #: NULL for a wake-anchored slot because wake is not an invented clock time.
    scheduled_local_time: Mapped[time | None] = mapped_column(Time)
    #: For a wake-anchored slot, the local time at which an unrecorded-dose reminder
    #: becomes due. This is reminder metadata, never the modeled or recorded dose time.
    reminder_local_time: Mapped[time | None] = mapped_column(Time)
    amount: Mapped[Decimal] = mapped_column(AmountType, nullable=False)
    unit: Mapped[DoseUnit] = mapped_column(StrEnumType(DoseUnit, 16), nullable=False)
    route: Mapped[Route] = mapped_column(StrEnumType(Route, 24), nullable=False, default=Route.ORAL)

    #: Free text like "with food" or "only if unwell". Conditions are physician text,
    #: never interpreted by the system as a rule to act on.
    condition: Mapped[str | None] = mapped_column(String(500))
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)

    regimen_version: Mapped[RegimenVersion] = relationship(back_populates="slots")
    medication: Mapped[Medication] = relationship(lazy="joined")

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        CheckConstraint(
            "(timing_mode = 'fixed_time' AND scheduled_local_time IS NOT NULL "
            "AND reminder_local_time IS NULL) OR "
            "(timing_mode = 'wake' AND scheduled_local_time IS NULL "
            "AND reminder_local_time IS NOT NULL)",
            name="timing_fields_match_mode",
        ),
        Index("ix_slot_version_time", "regimen_version_id", "scheduled_local_time"),
        PLAN_SCHEMA,
    )


class ApprovedInstruction(PlanBase):
    """Physician-authored instruction text attached to a plan version.

    Emergency instructions are these (SAFE-22). AI can neither author nor edit them,
    and the emergency page renders them straight from this table with no AI call and
    no integration (SAFE-21).
    """

    __tablename__ = "approved_instruction"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    regimen_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan.regimen_version.id", ondelete="CASCADE"), nullable=False, index=True
    )

    category: Mapped[InstructionCategory] = mapped_column(
        StrEnumType(InstructionCategory, 24), nullable=False
    )
    title: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Rendered as plain text, never as HTML (docs/threat-model.md, XSS).
    body: Mapped[str] = mapped_column(Text, nullable=False)
    authored_by: Mapped[str] = mapped_column(String(200), nullable=False)
    authored_on: Mapped[date] = mapped_column(Date, nullable=False)
    sort_order: Mapped[int] = mapped_column(nullable=False, default=0)

    regimen_version: Mapped[RegimenVersion] = relationship(back_populates="instructions")

    __table_args__ = (PLAN_SCHEMA,)


# ---------------------------------------------------------------------------
# fact schema
# ---------------------------------------------------------------------------


class DoseEvent(EventMixin, FactBase):
    """A dose actually taken. A recorded fact, never a plan record (SAFE-03).

    ``regimen_version_id`` records which plan was in force when this happened, so a
    later plan change cannot retroactively make a past dose look wrong.
    ``slot_id`` links to the scheduled slot when one applies -- it is nullable because
    stress doses and emergency injections have no slot, and inventing one would imply
    a schedule that does not exist.
    """

    __tablename__ = "dose_event"

    medication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan.medication.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    amount: Mapped[Decimal] = mapped_column(AmountType, nullable=False)
    unit: Mapped[DoseUnit] = mapped_column(StrEnumType(DoseUnit, 16), nullable=False)
    route: Mapped[Route] = mapped_column(StrEnumType(Route, 24), nullable=False)
    category: Mapped[DoseCategory] = mapped_column(StrEnumType(DoseCategory, 24), nullable=False)

    regimen_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan.regimen_version.id", ondelete="RESTRICT")
    )
    slot_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("plan.regimen_dose_slot.id", ondelete="RESTRICT")
    )
    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fact.stress_episode.id", ondelete="SET NULL"), index=True
    )

    medication: Mapped[Medication] = relationship(lazy="joined")

    __table_args__ = (
        # A recorded dose is a positive quantity. A missed dose is an absence, not a
        # zero-amount row (SAFE-10).
        CheckConstraint("amount > 0", name="amount_positive"),
        *event_table_args("dose_event"),
    )
