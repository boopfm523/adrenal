"""Owner-scoped observed inputs for the wake-anchored reference engine."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.analytics import wake_reference
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState
from healthcurve.events.models import MealEvent

MEAL_ROLES: Final = ("breakfast", "lunch", "dinner")


def observed_meals_for_day(
    session: Session,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
) -> dict[str, datetime]:
    """Map up to three chronological confirmed meals to reference pulse slots.

    The exact observed timestamps are preserved. The role names are merely the three
    pulse positions supported by the validated reference engine; they do not assert
    what the owner called a particular meal. A fourth meal is never silently turned
    into an unsupported population pulse.
    """

    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    rows = list(
        session.scalars(
            select(MealEvent)
            .where(
                MealEvent.owner_id == owner_id,
                MealEvent.occurred_at >= start,
                MealEvent.occurred_at < end,
                MealEvent.confirmation_state.in_(
                    (
                        ConfirmationState.DIRECT,
                        ConfirmationState.CONFIRMED_FROM_DRAFT,
                    )
                ),
                events.current_fact_predicate(MealEvent, owner_id=owner_id),
            )
            .order_by(MealEvent.occurred_at, MealEvent.id)
            .limit(len(MEAL_ROLES))
        )
    )
    return {
        role: row.occurred_at.astimezone(zone) for role, row in zip(MEAL_ROLES, rows, strict=False)
    }


def reference_for_owner(
    session: Session,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
    wake_at: datetime | None,
    sleep_onset_at: datetime | None,
) -> dict[str, object]:
    """Build a non-cached reference using only current observed meal facts."""

    meals = observed_meals_for_day(
        session,
        owner_id=owner_id,
        day=day,
        timezone=timezone,
    )
    return wake_reference.build_reference(
        day=day,
        timezone=timezone,
        wake_at=wake_at,
        sleep_onset_at=sleep_onset_at,
        meals=meals,
    )
