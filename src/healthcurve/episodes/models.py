"""Stress/up-dose episodes and emergency injections.

An episode groups a trigger, an interval, symptoms, doses, and a recovery note. It can
stay open across several days.

An episode never implies approval: grouping extra doses under an episode records what
happened, and says nothing about whether a clinician sanctioned it. Only an
``ApprovedInstruction`` can say that (SAFE-22).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, DateTime, ForeignKey, Numeric, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import FACT_SCHEMA, FactBase, StrEnumType
from healthcurve.events.base import EventMixin, event_table_args

AmountType = Numeric(10, 4)


class EpisodeStatus(StrEnum):
    OPEN = "open"
    RESOLVED = "resolved"
    ESCALATED = "escalated"  # went to hospital or emergency services


class EpisodeSeverity(StrEnum):
    MILD = "mild"
    MODERATE = "moderate"
    SEVERE = "severe"


class StressEpisode(FactBase):
    """A stress/up-dose episode.

    Not an ``EventMixin``: an episode is an interval that groups events rather than a
    point-in-time event itself. It carries its own provenance fields so nothing is lost.
    """

    __tablename__ = "stress_episode"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="RESTRICT"), nullable=False, index=True
    )

    trigger: Mapped[str] = mapped_column(String(200), nullable=False)
    status: Mapped[EpisodeStatus] = mapped_column(
        StrEnumType(EpisodeStatus, 16), nullable=False, default=EpisodeStatus.OPEN
    )
    severity: Mapped[EpisodeSeverity | None] = mapped_column(StrEnumType(EpisodeSeverity, 16))

    started_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    ended_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)

    #: Illness context. Temperature is Decimal for the same reason doses are.
    highest_temperature_c: Mapped[Decimal | None] = mapped_column(Numeric(4, 1))
    illness_description: Mapped[str | None] = mapped_column(String(500))

    recovery_notes: Mapped[str | None] = mapped_column(Text)
    outcome: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)

    recorded_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        CheckConstraint("ended_at IS NULL OR ended_at >= started_at", name="interval_ordered"),
        CheckConstraint(
            "status <> 'resolved' OR ended_at IS NOT NULL", name="resolved_requires_end"
        ),
        CheckConstraint(
            "highest_temperature_c IS NULL OR highest_temperature_c BETWEEN 25 AND 45",
            name="temperature_plausible",
        ),
        FACT_SCHEMA,
    )

    @property
    def is_open(self) -> bool:
        return self.status is EpisodeStatus.OPEN


class EmergencyInjectionEvent(EventMixin, FactBase):
    """An emergency hydrocortisone injection.

    Logging one must be fast and must work with AI, integrations, and background jobs
    all unavailable (SAFE-23). Everything except the medication, amount, unit, and time
    is optional -- in an emergency, a partial record now beats a complete record later.
    """

    __tablename__ = "emergency_injection_event"

    medication_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("plan.medication.id", ondelete="RESTRICT"), nullable=False
    )
    amount: Mapped[Decimal] = mapped_column(AmountType, nullable=False)
    unit: Mapped[str] = mapped_column(String(16), nullable=False)
    route: Mapped[str] = mapped_column(String(24), nullable=False)

    injection_site: Mapped[str | None] = mapped_column(String(120))
    reason: Mapped[str | None] = mapped_column(String(500))
    injected_by: Mapped[str | None] = mapped_column(
        String(120), doc="Self, or the name of whoever administered it."
    )
    response: Mapped[str | None] = mapped_column(Text)

    emergency_services_called: Mapped[bool | None] = mapped_column()
    transported_to_hospital: Mapped[bool | None] = mapped_column()
    contact_notified: Mapped[str | None] = mapped_column(String(200))

    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fact.stress_episode.id", ondelete="SET NULL"), index=True
    )

    __table_args__ = (
        CheckConstraint("amount > 0", name="amount_positive"),
        *event_table_args("emergency_injection_event"),
    )
