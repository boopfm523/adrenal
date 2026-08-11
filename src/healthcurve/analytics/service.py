"""Deterministic health metrics with explicit definitions and missingness.

This module deliberately contains no statistical inference and no model calls. The
pure :func:`summarize` function makes the arithmetic independently fixture-testable;
the database adapter only selects owner-scoped, current facts and plan comparisons.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.episodes.models import StressEpisode
from healthcurve.events import service as events
from healthcurve.events.models import SymptomEvent
from healthcurve.medications import service as medications

DOSE_TOTAL_DEFINITION: Final = (
    "For each local calendar day, actual total is the sum of current recorded dose "
    "facts. Planned total is the sum of slots whose scheduled local instant falls in "
    "the half-open effective interval of the historically physician-approved regimen, "
    "including a plan change within a day. No recorded doses is shown as missing, not "
    "as a zero-dose fact. If a day contains incompatible units, totals are unavailable "
    "rather than combined."
)
EPISODE_DEFINITION: Final = (
    "Counts episodes that started in the selected local-date range. Duration is end "
    "minus start for resolved episodes; open episodes have missing duration and are "
    "not assigned an estimated value."
)
SYMPTOM_DEFINITION: Final = (
    "Counts current symptom facts in the selected local-date range, groups frequency "
    "by the recorded symptom name, and averages only recorded 0-10 severity values. "
    "Symptoms without severity remain counted and are reported as missing severity."
)


@dataclass(frozen=True, slots=True)
class TimingInput:
    status: str
    minutes_from_scheduled: int | None
    regimen_version_id: uuid.UUID | None = None
    regimen_version_label: str | None = None
    effective_from: datetime | None = None
    effective_to: datetime | None = None


@dataclass(frozen=True, slots=True)
class DayInput:
    day: date
    planned_total: Decimal | None
    actual_total: Decimal
    recorded_dose_count: int
    statuses: tuple[str, ...]
    unit: str | None = "mg"
    incompatible_units: bool = False
    timings: tuple[TimingInput, ...] = ()


@dataclass(frozen=True, slots=True)
class EpisodeInput:
    started_at: datetime
    ended_at: datetime | None


@dataclass(frozen=True, slots=True)
class SymptomInput:
    name: str
    severity: int | None


def summarize(
    *,
    date_from: date,
    date_to: date,
    timezone: str,
    days: list[DayInput],
    episodes: list[EpisodeInput],
    symptoms: list[SymptomInput],
) -> dict[str, object]:
    """Compute the entire summary using exact arithmetic and no hidden state."""
    planned_days = sum(day.planned_total is not None for day in days)
    recorded_days = sum(day.recorded_dose_count > 0 for day in days)
    timing_statuses = [status for day in days for status in day.statuses]
    timing_inputs = [timing for day in days for timing in day.timings]
    if timing_inputs:
        timing_statuses = [timing.status for timing in timing_inputs]
    else:
        timing_inputs = [
            TimingInput(status=status, minutes_from_scheduled=None)
            for day in days
            for status in day.statuses
        ]
    deviations = [
        Decimal(abs(timing.minutes_from_scheduled))
        for timing in timing_inputs
        if timing.minutes_from_scheduled is not None
    ]
    period_groups: dict[
        tuple[uuid.UUID | None, str | None, datetime | None, datetime | None], list[TimingInput]
    ] = {}
    for timing in timing_inputs:
        key = (
            timing.regimen_version_id,
            timing.regimen_version_label,
            timing.effective_from,
            timing.effective_to,
        )
        period_groups.setdefault(key, []).append(timing)

    plan_periods: list[dict[str, object]] = []
    for key, rows in sorted(
        period_groups.items(),
        key=lambda item: (
            item[0][2] is None,
            item[0][2].isoformat() if item[0][2] is not None else "",
            str(item[0][0] or ""),
        ),
    ):
        period_deviations = [
            Decimal(abs(row.minutes_from_scheduled))
            for row in rows
            if row.minutes_from_scheduled is not None
        ]
        statuses = [row.status for row in rows]
        total_deviation = sum(period_deviations, Decimal(0)) if period_deviations else None
        plan_periods.append(
            {
                "regimen_version_id": key[0],
                "regimen_version_label": key[1],
                "effective_from": key[2],
                "effective_to": key[3],
                "sample_count": len(rows),
                "matched_count": len(period_deviations),
                "missing_count": statuses.count("missing"),
                "on_time": statuses.count("on_time"),
                "early": statuses.count("early"),
                "late": statuses.count("late"),
                "unplanned": statuses.count("unplanned"),
                "total_absolute_deviation_minutes": total_deviation,
                "average_absolute_deviation_minutes": (
                    total_deviation / len(period_deviations)
                    if total_deviation is not None
                    else None
                ),
            }
        )
    duration_values = [
        Decimal((episode.ended_at - episode.started_at).total_seconds()) / Decimal(60)
        for episode in episodes
        if episode.ended_at is not None
    ]
    severities = [Decimal(symptom.severity) for symptom in symptoms if symptom.severity is not None]
    frequency: dict[str, int] = {}
    for symptom in symptoms:
        frequency[symptom.name] = frequency.get(symptom.name, 0) + 1

    return {
        "date_from": date_from,
        "date_to": date_to,
        "timezone": timezone,
        "daily_doses": {
            "definition": DOSE_TOTAL_DEFINITION,
            "timezone": timezone,
            "sample_count": sum(day.recorded_dose_count for day in days),
            "missing_count": len(days) - recorded_days,
            "days_without_approved_plan": len(days) - planned_days,
            "values": [
                {
                    "date": day.day,
                    "planned_total": None if day.incompatible_units else day.planned_total,
                    "actual_total": (
                        day.actual_total
                        if day.recorded_dose_count > 0 and not day.incompatible_units
                        else None
                    ),
                    "recorded_dose_count": day.recorded_dose_count,
                    "unit": day.unit,
                    "incompatible_units": day.incompatible_units,
                }
                for day in days
            ],
        },
        "timing": {
            "definition": medications.TIMING_METRIC_DEFINITION,
            "timezone": timezone,
            "sample_count": len(timing_statuses),
            "missing_count": timing_statuses.count("missing"),
            "matched_count": len(deviations),
            "on_time": timing_statuses.count("on_time"),
            "early": timing_statuses.count("early"),
            "late": timing_statuses.count("late"),
            "unplanned": timing_statuses.count("unplanned"),
            "total_absolute_deviation_minutes": (
                sum(deviations, Decimal(0)) if deviations else None
            ),
            "average_absolute_deviation_minutes": (
                sum(deviations, Decimal(0)) / len(deviations) if deviations else None
            ),
            "plan_periods": plan_periods,
        },
        "episodes": {
            "definition": EPISODE_DEFINITION,
            "timezone": timezone,
            "sample_count": len(episodes),
            "missing_count": sum(episode.ended_at is None for episode in episodes),
            "count": len(episodes),
            "total_duration_minutes": sum(duration_values, Decimal(0)),
            "average_duration_minutes": (
                sum(duration_values, Decimal(0)) / len(duration_values) if duration_values else None
            ),
        },
        "symptoms": {
            "definition": SYMPTOM_DEFINITION,
            "timezone": timezone,
            "sample_count": len(symptoms),
            "missing_count": len(symptoms) - len(severities),
            "count": len(symptoms),
            "average_severity": (
                sum(severities, Decimal(0)) / len(severities) if severities else None
            ),
            "frequency": dict(sorted(frequency.items())),
        },
    }


def summary_for_owner(
    session: Session,
    *,
    owner_id: uuid.UUID,
    date_from: date,
    date_to: date,
    timezone: str,
) -> dict[str, object]:
    """Load current facts for one owner and pass them to the pure calculator."""
    zone = ZoneInfo(timezone)
    start = datetime.combine(date_from, time.min, tzinfo=zone)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
    days: list[DayInput] = []
    current_day = date_from
    while current_day <= date_to:
        comparison = medications.compare_day(
            session, owner_id=owner_id, day=current_day, timezone=timezone
        )
        slots = comparison["slots"]
        statuses = tuple(slot.status for slot in slots)  # type: ignore[union-attr]
        timings = tuple(
            TimingInput(
                status=slot.status,
                minutes_from_scheduled=slot.minutes_from_scheduled,
                regimen_version_id=slot.regimen_version_id,
                regimen_version_label=slot.regimen_version_label,
                effective_from=slot.regimen_effective_from,
                effective_to=slot.regimen_effective_to,
            )
            for slot in slots  # type: ignore[union-attr]
        )
        units = {str(slot.unit) for slot in slots}  # type: ignore[union-attr]
        incompatible_units = len(units) > 1
        recorded_count = sum(status != "missing" for status in statuses)
        days.append(
            DayInput(
                day=current_day,
                planned_total=(
                    None if incompatible_units else comparison["planned_total"]  # type: ignore[arg-type]
                ),
                actual_total=comparison["actual_total"],  # type: ignore[arg-type]
                recorded_dose_count=recorded_count,
                statuses=statuses,
                unit=next(iter(units)) if len(units) == 1 else None,
                incompatible_units=incompatible_units,
                timings=timings,
            )
        )
        current_day += timedelta(days=1)

    episode_rows = list(
        session.scalars(
            select(StressEpisode).where(
                StressEpisode.owner_id == owner_id,
                StressEpisode.started_at >= start,
                StressEpisode.started_at < end,
            )
        )
    )
    symptom_rows = list(
        session.scalars(
            select(SymptomEvent).where(
                SymptomEvent.owner_id == owner_id,
                SymptomEvent.occurred_at >= start,
                SymptomEvent.occurred_at < end,
            )
        )
    )
    symptom_rows = events.current_only(session, SymptomEvent, symptom_rows)
    return summarize(
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        days=days,
        episodes=[EpisodeInput(row.started_at, row.ended_at) for row in episode_rows],
        symptoms=[SymptomInput(row.name, row.severity) for row in symptom_rows],
    )
