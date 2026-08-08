"""Concrete event types built on the canonical base.

Only the types needed to exercise and prove the base belong here. Domain-specific
events (doses, episodes, injections) live in their own modules and mix in the same
:class:`~healthcurve.events.base.EventMixin`.
"""

from __future__ import annotations

from sqlalchemy import CheckConstraint, SmallInteger, String
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import FactBase
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

    __table_args__ = (
        CheckConstraint("severity IS NULL OR severity BETWEEN 0 AND 10", name="severity_scale"),
        *event_table_args("symptom_event"),
    )


class DiaryEvent(EventMixin, FactBase):
    """A free-text diary entry.

    ``is_sensitive`` excludes an entry from default views and reports (threat model T7).
    The text is class C2 and is never logged.
    """

    __tablename__ = "diary_event"

    text: Mapped[str] = mapped_column(String(10_000), nullable=False)
    is_sensitive: Mapped[bool] = mapped_column(nullable=False, default=False)

    __table_args__ = event_table_args("diary_event")
