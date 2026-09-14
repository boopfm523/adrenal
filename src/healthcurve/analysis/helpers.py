"""Deterministic helpers for computations local models get wrong (ADR-0036).

The SQL here is fixed and parameterized, never model-authored, and runs on the
view-only analyst role. Modeled exposure is Python over domain services and runs in a
caller-provided read-only session instead, because the analyst role deliberately cannot
read the base tables that model needs.
"""

from __future__ import annotations

import datetime as dt
import uuid
from collections.abc import Callable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from typing import Annotated, Any, Final, Literal, Self, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator
from sqlalchemy import Engine, TextClause, bindparam, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from healthcurve.analysis.query import STATEMENT_TIMEOUT_MS, QueryError, database_error, jsonable
from healthcurve.analysis.timeofday import summarize_clock_minutes
from healthcurve.analytics import injectable_pharmacokinetics, wake_reference_inputs

# ruff: noqa: S608 -- helper SQL is assembled only from constant fragments in this module;
# every runtime value (dates, filters, limits, metrics) is a bound parameter.

MAX_RANGE_DAYS: Final = 366
MAX_EVENTS: Final = 200
MAX_PER_EVENT_ROWS: Final = 60
ONE_DECIMAL: Final = Decimal("0.1")

ClockSource = Literal[
    "bedtime", "wake", "dose", "first_dose_of_day", "meal", "symptom", "activity_start"
]
EventSource = Literal[
    "symptom",
    "dose",
    "activity_start",
    "activity_end",
    "meal",
    "stress_episode_start",
    "wake",
    "bedtime",
]
DoseCategory = Literal["scheduled", "late", "replacement", "stress", "taper", "emergency"]
WindowMetric = Literal["heart_rate", "stress", "respiration_rate", "hrv"]
LocalClock = Annotated[str, StringConstraints(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")]

MODELED_LABEL: Final = (
    "Modeled theoretical free-cortisol estimate from recorded doses. It is not a "
    "measurement, not a diagnosis, and not dosing guidance."
)
WINDOW_CAUTION: Final = (
    "Descriptive comparison of recorded wearable samples around events. Association is "
    "not causation, and missing samples are excluded rather than treated as zero."
)


class _RangeArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date_from: dt.date = Field(description="First local date (YYYY-MM-DD), inclusive.")
    date_to: dt.date = Field(description="Last local date (YYYY-MM-DD), inclusive.")
    medication: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="Case-insensitive part of the medication name (dose sources only).",
    )
    dose_category: DoseCategory | None = Field(
        default=None, description="Only doses in this category (dose sources only)."
    )
    symptom_name: str | None = Field(
        default=None,
        min_length=1,
        max_length=120,
        description="Case-insensitive part of the symptom name (symptom sources only).",
    )

    @model_validator(mode="after")
    def _bounded_range(self) -> Self:
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        if (self.date_to - self.date_from).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"the date range cannot exceed {MAX_RANGE_DAYS} days")
        return self


class ClockTimeStatsArguments(_RangeArguments):
    source: ClockSource = Field(
        description=(
            "bedtime or wake (main overnight sleep by wake date), dose (every dose), "
            "first_dose_of_day, meal, symptom, or activity_start."
        )
    )


class EventWindowStatsArguments(_RangeArguments):
    event_source: EventSource = Field(description="Events to center the windows on.")
    metrics: list[WindowMetric] = Field(
        default_factory=lambda: ["heart_rate"],
        min_length=1,
        max_length=4,
        description="Wearable metrics to summarize in each window.",
    )
    minutes_before: int = Field(default=60, ge=5, le=720)
    minutes_after: int = Field(default=60, ge=5, le=720)
    include_per_event: bool = Field(
        default=True, description="Include per-event rows (up to 60) as well as the summary."
    )


class ModeledExposureArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    date: dt.date = Field(description="Local date (YYYY-MM-DD) to model.")
    local_times: list[LocalClock] | None = Field(
        default=None,
        max_length=24,
        description="Local clock times (HH:MM) to report; omit for every hour of the day.",
    )


_DOSE_FILTER: Final = (
    "(CAST(:medication_pattern AS text) IS NULL "
    r"OR medication_name ILIKE CAST(:medication_pattern AS text) ESCAPE '\') "
    "AND (CAST(:dose_category AS text) IS NULL OR category = CAST(:dose_category AS text))"
)
_SYMPTOM_FILTER: Final = (
    "(CAST(:symptom_pattern AS text) IS NULL "
    r"OR name ILIKE CAST(:symptom_pattern AS text) ESCAPE '\')"
)
_IN_RANGE: Final = "BETWEEN :date_from AND :date_to"

_CLOCK_SQL: Final[dict[str, str]] = {
    "bedtime": f"SELECT wake_date AS local_date, bedtime_clock AS clock "
    f"FROM analytics.sleep_nights WHERE wake_date {_IN_RANGE}",
    "wake": f"SELECT wake_date AS local_date, wake_clock AS clock "
    f"FROM analytics.sleep_nights WHERE wake_date {_IN_RANGE}",
    "dose": f"SELECT local_date, local_clock AS clock FROM analytics.doses "
    f"WHERE local_date {_IN_RANGE} AND {_DOSE_FILTER}",
    "first_dose_of_day": f"SELECT DISTINCT ON (local_date) local_date, local_clock AS clock "
    f"FROM analytics.doses WHERE local_date {_IN_RANGE} AND {_DOSE_FILTER} "
    "ORDER BY local_date, taken_at",
    "meal": f"SELECT local_date, local_time::time AS clock FROM analytics.meals "
    f"WHERE local_date {_IN_RANGE}",
    "symptom": f"SELECT local_date, local_time::time AS clock FROM analytics.symptoms "
    f"WHERE local_date {_IN_RANGE} AND {_SYMPTOM_FILTER}",
    "activity_start": f"SELECT local_date, start_local::time AS clock "
    f"FROM analytics.activities WHERE local_date {_IN_RANGE}",
}

_EVENT_SQL: Final[dict[str, str]] = {
    "symptom": "SELECT symptom_id AS event_id, occurred_at AS event_at, local_time AS "
    "event_local, name || coalesce(' (severity ' || severity::text || ')', '') AS label "
    f"FROM analytics.symptoms WHERE local_date {_IN_RANGE} AND {_SYMPTOM_FILTER}",
    "dose": "SELECT dose_id AS event_id, taken_at AS event_at, taken_local AS event_local, "
    "medication_name || ' ' || trim_scale(amount)::text || ' ' || unit || ' (' || category "
    f"|| ')' AS label FROM analytics.doses WHERE local_date {_IN_RANGE} AND {_DOSE_FILTER}",
    "activity_start": "SELECT activity_id AS event_id, start_at AS event_at, start_local AS "
    "event_local, sport || ' start' AS label FROM analytics.activities "
    f"WHERE local_date {_IN_RANGE}",
    "activity_end": "SELECT activity_id AS event_id, end_at AS event_at, end_at AT TIME ZONE "
    "timezone AS event_local, sport || ' end' AS label FROM analytics.activities "
    f"WHERE local_date {_IN_RANGE}",
    "meal": "SELECT meal_id AS event_id, occurred_at AS event_at, local_time AS event_local, "
    "'meal' || coalesce(' (' || size || ')', '') AS label FROM analytics.meals "
    f"WHERE local_date {_IN_RANGE}",
    "stress_episode_start": "SELECT episode_id AS event_id, started_at AS event_at, "
    "started_local AS event_local, 'stress episode (' || status || ')' AS label "
    f"FROM analytics.stress_episodes WHERE start_local_date {_IN_RANGE}",
    "wake": "SELECT sleep_id AS event_id, wake_at AS event_at, wake_local AS event_local, "
    f"'wake' AS label FROM analytics.sleep_nights WHERE wake_date {_IN_RANGE}",
    "bedtime": "SELECT sleep_id AS event_id, bedtime_at AS event_at, bedtime_local AS "
    "event_local, 'bedtime' AS label FROM analytics.sleep_nights "
    f"WHERE bedtime_local::date {_IN_RANGE}",
}

_WINDOW_SQL: Final = """
WITH events AS ({events} ORDER BY event_at, event_id LIMIT :event_limit)
SELECT e.event_id,
       w.metric_type,
       CASE WHEN w.sample_at < e.event_at THEN 'before' ELSE 'after' END AS side,
       count(*) AS sample_count,
       avg(w.value) AS average,
       min(w.value) AS minimum,
       max(w.value) AS maximum
FROM events e
JOIN analytics.wearable_samples w
  ON w.metric_type IN :metrics
 AND w.sample_at >= e.event_at - make_interval(mins => CAST(:minutes_before AS integer))
 AND w.sample_at < e.event_at + make_interval(mins => CAST(:minutes_after AS integer))
GROUP BY e.event_id, w.metric_type, side
"""


def clock_time_stats(engine: Engine, arguments: ClockTimeStatsArguments) -> dict[str, Any]:
    rows = _fetch(engine, text(_CLOCK_SQL[arguments.source]), _filter_parameters(arguments))
    clocks = [cast(dt.time, row.clock) for row in rows if row.clock is not None]
    summary = summarize_clock_minutes(
        [value.hour * 60 + value.minute + value.second / 60 for value in clocks]
    )
    return {
        "source": arguments.source,
        "date_from": arguments.date_from.isoformat(),
        "date_to": arguments.date_to.isoformat(),
        "filters": _filters(arguments),
        "days_with_values": len({row.local_date for row in rows if row.clock is not None}),
        "summary": summary.as_data(),
        "method": (
            "Circular mean of local clock times; median, earliest, and latest are measured "
            "around that mean so times either side of midnight stay adjacent. "
            "spread_minutes is the circular standard deviation."
        ),
    }


def event_window_stats(engine: Engine, arguments: EventWindowStatsArguments) -> dict[str, Any]:
    parameters = {
        **_filter_parameters(arguments),
        "event_limit": MAX_EVENTS + 1,
        "metrics": list(dict.fromkeys(arguments.metrics)),
        "minutes_before": arguments.minutes_before,
        "minutes_after": arguments.minutes_after,
    }
    event_sql = _EVENT_SQL[arguments.event_source]
    events = _fetch(
        engine, text(f"{event_sql} ORDER BY event_at, event_id LIMIT :event_limit"), parameters
    )
    events_truncated = len(events) > MAX_EVENTS
    events = events[:MAX_EVENTS]
    window = text(_WINDOW_SQL.format(events=event_sql)).bindparams(
        bindparam("metrics", expanding=True)
    )
    stats: dict[uuid.UUID, dict[str, dict[str, dict[str, Any]]]] = {}
    for row in _fetch(engine, window, parameters):
        stats.setdefault(row.event_id, {}).setdefault(row.metric_type, {})[row.side] = {
            "sample_count": row.sample_count,
            "average": row.average,
            "minimum": row.minimum,
            "maximum": row.maximum,
        }

    metrics = cast(list[str], parameters["metrics"])
    per_event: list[dict[str, Any]] = []
    summary: dict[str, dict[str, Any]] = {}
    for metric in metrics:
        befores: list[Decimal] = []
        afters: list[Decimal] = []
        changes: list[Decimal] = []
        for event in events:
            sides = stats.get(event.event_id, {}).get(metric, {})
            before = sides.get("before", {}).get("average")
            after = sides.get("after", {}).get("average")
            if before is not None:
                befores.append(before)
            if after is not None:
                afters.append(after)
            if before is not None and after is not None:
                changes.append(after - before)
        summary[metric] = {
            "events_with_before": len(befores),
            "events_with_after": len(afters),
            "events_with_both": len(changes),
            "mean_before": _round(_mean(befores)),
            "mean_after": _round(_mean(afters)),
            "mean_change_after_minus_before": _round(_mean(changes)),
        }
    if arguments.include_per_event:
        for event in events[:MAX_PER_EVENT_ROWS]:
            sides_by_metric = stats.get(event.event_id, {})
            per_event.append(
                {
                    "event_id": event.event_id,
                    "event_local": event.event_local,
                    "label": event.label,
                    "metrics": {
                        metric: {
                            side: _window_side(sides_by_metric.get(metric, {}).get(side))
                            for side in ("before", "after")
                        }
                        for metric in metrics
                    },
                }
            )
    return cast(
        dict[str, Any],
        jsonable(
            {
                "event_source": arguments.event_source,
                "date_from": arguments.date_from.isoformat(),
                "date_to": arguments.date_to.isoformat(),
                "filters": _filters(arguments),
                "window": {
                    "minutes_before": arguments.minutes_before,
                    "minutes_after": arguments.minutes_after,
                },
                "events_total": len(events),
                "events_truncated": events_truncated,
                "summary": summary,
                "per_event": per_event,
                "per_event_truncated": arguments.include_per_event
                and len(events) > MAX_PER_EVENT_ROWS,
                "method": (
                    "For each event, samples in [event - minutes_before, event) are 'before' "
                    "and [event, event + minutes_after) are 'after'. Summary means average "
                    "the per-event averages; changes use only events with both sides."
                ),
                "caution": WINDOW_CAUTION,
            }
        ),
    )


def modeled_exposure(
    session_factory: Callable[[], Session],
    *,
    owner_id: uuid.UUID,
    timezone: str,
    arguments: ModeledExposureArguments,
) -> dict[str, Any]:
    zone = ZoneInfo(timezone)
    try:
        with session_factory() as session, session.begin():
            if session.get_bind().dialect.name == "postgresql":
                session.execute(text("SET TRANSACTION READ ONLY"))
            curve = injectable_pharmacokinetics.curve_for_owner(
                session, owner_id=owner_id, day=arguments.date, timezone=timezone
            )
            samples = cast(list[dict[str, Any]], curve["samples"])
            reference = wake_reference_inputs.reference_from_observed_facts_for_owner(
                session,
                owner_id=owner_id,
                day=arguments.date,
                timezone=timezone,
                sample_instants=[cast(dt.datetime, sample["occurred_at"]) for sample in samples],
            )
    except DBAPIError as exc:
        raise database_error(exc) from None
    reference_samples = cast(list[dict[str, Any]], reference.get("samples", []))

    clocks = arguments.local_times or [f"{hour:02d}:00" for hour in range(24)]
    points: list[dict[str, Any]] = []
    for clock in clocks:
        hour, minute = (int(part) for part in clock.split(":"))
        anchor = dt.datetime.combine(arguments.date, dt.time(hour, minute), tzinfo=zone)
        sample = _nearest(samples, anchor)
        band = _nearest(reference_samples, anchor)
        modeled = None if sample is None else cast(Decimal, sample["modeled_free_cortisol_nmol_l"])
        points.append(
            {
                "local_time": clock,
                "modeled_free_cortisol_nmol_l": _round(modeled),
                "reference_p5_nmol_l": None if band is None else band["serum_free_p5_nmol_l"],
                "reference_p50_nmol_l": None if band is None else band["serum_free_p50_nmol_l"],
                "reference_p95_nmol_l": None if band is None else band["serum_free_p95_nmol_l"],
                "position": _band_position(modeled, band),
            }
        )

    values = [
        (cast(Decimal, sample["modeled_free_cortisol_nmol_l"]), sample)
        for sample in samples
        if sample.get("modeled_free_cortisol_nmol_l") is not None
        and cast(dt.datetime, sample["occurred_at"]).astimezone(zone).date() == arguments.date
    ]
    peak = max(values, key=lambda item: item[0], default=None)
    return cast(
        dict[str, Any],
        jsonable(
            {
                "model_id": injectable_pharmacokinetics.MODEL_ID,
                "model_revision": injectable_pharmacokinetics.MODEL_REVISION,
                "series_name": curve.get("series_name"),
                "unit": curve.get("series_unit"),
                "date": arguments.date.isoformat(),
                "timezone": timezone,
                "points": points,
                "day_summary": {
                    "minimum": _round(min((value for value, _ in values), default=None)),
                    "maximum": _round(None if peak is None else peak[0]),
                    "time_of_maximum": None
                    if peak is None
                    else cast(dt.datetime, peak[1]["occurred_at"])
                    .astimezone(zone)
                    .strftime("%H:%M"),
                    "mean": _round(_mean([value for value, _ in values])),
                },
                "reference_available": bool(reference_samples),
                "label": MODELED_LABEL,
            }
        ),
    )


def _fetch(engine: Engine, statement: TextClause, parameters: dict[str, Any]) -> list[Any]:
    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {STATEMENT_TIMEOUT_MS}")
            return list(connection.execute(statement, parameters).all())
    except DBAPIError as exc:
        raise database_error(exc) from None


def _like_pattern(value: str | None) -> str | None:
    if value is None:
        return None
    escaped = value.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    return f"%{escaped}%"


def _filter_parameters(arguments: _RangeArguments) -> dict[str, Any]:
    return {
        "date_from": arguments.date_from,
        "date_to": arguments.date_to,
        "medication_pattern": _like_pattern(arguments.medication),
        "dose_category": arguments.dose_category,
        "symptom_pattern": _like_pattern(arguments.symptom_name),
    }


def _filters(arguments: _RangeArguments) -> dict[str, str]:
    values = {
        "medication": arguments.medication,
        "dose_category": arguments.dose_category,
        "symptom_name": arguments.symptom_name,
    }
    return {key: value for key, value in values.items() if value is not None}


def _mean(values: Sequence[Decimal]) -> Decimal | None:
    return sum(values, Decimal(0)) / len(values) if values else None


def _round(value: Decimal | None) -> Decimal | None:
    return None if value is None else value.quantize(ONE_DECIMAL, rounding=ROUND_HALF_UP)


def _window_side(side: dict[str, Any] | None) -> dict[str, Any]:
    if side is None:
        return {"sample_count": 0, "average": None, "minimum": None, "maximum": None}
    return {**side, "average": _round(side["average"])}


def _nearest(samples: Sequence[dict[str, Any]], anchor: dt.datetime) -> dict[str, Any] | None:
    return min(
        samples,
        key=lambda sample: abs((cast(dt.datetime, sample["occurred_at"]) - anchor).total_seconds()),
        default=None,
    )


def _band_position(modeled: Decimal | None, band: dict[str, Any] | None) -> str | None:
    if modeled is None or band is None:
        return None
    if modeled < cast(Decimal, band["serum_free_p5_nmol_l"]):
        return "below_recorded_reference_p5"
    if modeled < cast(Decimal, band["serum_free_p50_nmol_l"]):
        return "between_recorded_reference_p5_and_p50"
    if modeled <= cast(Decimal, band["serum_free_p95_nmol_l"]):
        return "between_recorded_reference_p50_and_p95"
    return "above_recorded_reference_p95"


def configured_modeled_exposure(
    session_factory: Callable[[], Session] | None,
    owner_id: uuid.UUID | None,
    timezone: str | None,
) -> tuple[Callable[[], Session], uuid.UUID, str]:
    if session_factory is None or owner_id is None or timezone is None:
        raise QueryError(
            "modeled_exposure_not_configured",
            "Modeled exposure is not available in this context.",
        )
    return session_factory, owner_id, timezone
