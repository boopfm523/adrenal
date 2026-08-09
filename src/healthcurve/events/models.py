"""Concrete event types built on the canonical base.

Domain-heavy events live with their domain: doses in :mod:`healthcurve.medications`,
injections in :mod:`healthcurve.episodes`. What is here are the general-purpose
timeline events.
"""

from __future__ import annotations

import uuid
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, ForeignKey, SmallInteger, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import FactBase, StrEnumType
from healthcurve.events.base import EventMixin, event_table_args


class SymptomEvent(EventMixin, FactBase):
    """A reported symptom.

    Severity uses an explicitly defined 0-10 scale. Plan section 6 requires the scale be
    *defined* rather than implied, so the range is a database constraint and the scale
    is documented here: 0 none, 1-3 mild, 4-6 moderate, 7-9 severe, 10 worst imaginable.
    """

    __tablename__ = "symptom_event"

    name: Mapped[str] = mapped_column(String(120), nullable=False)
    severity: Mapped[int | None] = mapped_column(SmallInteger)
    body_area: Mapped[str | None] = mapped_column(String(120))
    #: For symptoms that persist rather than occur at an instant.
    ended_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))

    episode_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fact.stress_episode.id", ondelete="SET NULL"), index=True
    )

    __table_args__ = (
        CheckConstraint("severity IS NULL OR severity BETWEEN 0 AND 10", name="severity_scale"),
        CheckConstraint("ended_at IS NULL OR ended_at >= occurred_at", name="interval_ordered"),
        *event_table_args("symptom_event"),
    )


class DiaryEvent(EventMixin, FactBase):
    """A free-text diary entry.

    ``is_sensitive`` excludes an entry from default views and reports (threat model T7).
    The text is class C2: never logged, and rendered as text rather than HTML.
    """

    __tablename__ = "diary_event"

    text: Mapped[str] = mapped_column(String(10_000), nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    tags: Mapped[str | None] = mapped_column(String(500), doc="Comma-separated, free-form.")

    __table_args__ = event_table_args("diary_event")


class LifeEventCategory(StrEnum):
    """Context that helps explain a pattern without asserting it caused anything."""

    TRAVEL = "travel"
    ILLNESS = "illness"
    WORK = "work"
    EXERCISE = "exercise"
    SLEEP_DISRUPTION = "sleep_disruption"
    STRESS = "stress"
    MEDICAL_APPOINTMENT = "medical_appointment"
    OTHER = "other"


class LifeEvent(EventMixin, FactBase):
    """Something that happened in the owner's life, for timeline context.

    Overlays built from these must carry the correlation caution (SAFE-25): a life
    event sitting near a symptom is context, never a cause.
    """

    __tablename__ = "life_event"

    title: Mapped[str] = mapped_column(String(200), nullable=False)
    category: Mapped[LifeEventCategory] = mapped_column(
        StrEnumType(LifeEventCategory, 32), nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text)
    ended_at: Mapped[object | None] = mapped_column(DateTime(timezone=True))
    is_sensitive: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    __table_args__ = (
        CheckConstraint("ended_at IS NULL OR ended_at >= occurred_at", name="interval_ordered"),
        *event_table_args("life_event"),
    )
