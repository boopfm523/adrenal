"""Owner-scoped observed inputs for the wake-anchored reference engine."""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import UTC, date, datetime, time, timedelta
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.analytics import wake_reference
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState
from healthcurve.events.models import MealEvent
from healthcurve.integrations.garmin.models import GarminSleepEvent

MEAL_ROLES: Final = ("breakfast", "lunch", "dinner")


def signed_minutes_from_wake(
    wake_at: datetime | None,
    dose_at: datetime | None,
    *,
    timezone: str,
) -> int | None:
    """Return signed whole minutes from observed wake to recorded dose.

    Missing observations stay missing. In particular, callers must not pass a
    plan reminder time as a substitute for either observed fact.
    """

    if wake_at is None or dose_at is None:
        return None
    zone = ZoneInfo(timezone)

    def instant(value: datetime) -> datetime:
        # Medication comparison deliberately exposes wall-clock local times as
        # naive datetimes. Garmin observations are timezone-aware instants. Put
        # both on the requested local timeline, then compare UTC instants so DST
        # transitions retain their real elapsed duration.
        local = value.replace(tzinfo=zone) if value.tzinfo is None else value.astimezone(zone)
        return local.astimezone(UTC)

    return int((instant(dose_at) - instant(wake_at)).total_seconds() / 60)


def observed_sleep_timing_for_day(
    session: Session,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
) -> tuple[datetime | None, datetime | None]:
    """Return an observed final wake and relevant sleep onset without inventing either.

    A selected day can contain the end of the prior night's sleep and the beginning of
    that night's sleep.  The final wake is taken from the longest current Garmin sleep
    session ending on the selected local day.  Sleep onset prefers a session beginning
    on the selected day and otherwise uses the onset of that same morning session.
    """

    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    search_start = start - timedelta(days=1)
    search_end = end + timedelta(days=1)
    rows = list(
        session.scalars(
            select(GarminSleepEvent)
            .where(
                GarminSleepEvent.owner_id == owner_id,
                GarminSleepEvent.occurred_at < search_end,
                GarminSleepEvent.ended_at > search_start,
                GarminSleepEvent.confirmation_state == ConfirmationState.PROVIDER_IMPORTED,
                events.current_fact_predicate(GarminSleepEvent, owner_id=owner_id),
            )
            .order_by(GarminSleepEvent.occurred_at, GarminSleepEvent.id)
        )
    )
    waking_sessions = [row for row in rows if row.ended_at.astimezone(zone).date() == day]
    primary = max(
        waking_sessions,
        key=lambda row: ((row.ended_at - row.occurred_at).total_seconds(), row.ended_at),
        default=None,
    )
    evening_sessions = [row for row in rows if row.occurred_at.astimezone(zone).date() == day]
    evening = max(evening_sessions, key=lambda row: row.occurred_at, default=None)
    wake_at = None if primary is None else primary.ended_at.astimezone(zone)
    sleep_onset_at = (
        evening.occurred_at.astimezone(zone)
        if evening is not None
        else None
        if primary is None
        else primary.occurred_at.astimezone(zone)
    )
    return wake_at, sleep_onset_at


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
    sample_instants: Iterable[datetime] | None = None,
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
        sample_instants=sample_instants,
    )


def reference_from_observed_facts_for_owner(
    session: Session,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
    sample_instants: Iterable[datetime] | None = None,
) -> dict[str, object]:
    """Build the selected-day reference from current Garmin sleep and meal facts."""

    wake_at, sleep_onset_at = observed_sleep_timing_for_day(
        session,
        owner_id=owner_id,
        day=day,
        timezone=timezone,
    )
    return reference_for_owner(
        session,
        owner_id=owner_id,
        day=day,
        timezone=timezone,
        wake_at=wake_at,
        sleep_onset_at=sleep_onset_at,
        sample_instants=sample_instants,
    )
