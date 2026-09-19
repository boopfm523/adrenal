"""Theoretical healthy cortisol response to measured physical load (ADR-0037).

For one selected local day, Garmin heart-rate samples become exercise intensity as a
fraction of heart-rate reserve. A first-order healthy-response model, calibrated to a
published exercise-intensity study, estimates how far a healthy adrenal response would
raise serum total cortisol above its circadian level. The wake-anchored healthy
reference percentiles are raised by that fraction in the total-cortisol domain and
converted back to free cortisol with the reference's binding equation.

The result is population-level theoretical context. It is not the owner's measured
cortisol, a personal requirement, or a dose, and it never changes the modeled curve.
Missing heart-rate intervals contribute no load; nothing is imputed.
"""

from __future__ import annotations

import math
import statistics
import uuid
from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Final, cast
from zoneinfo import ZoneInfo

from sqlalchemy import Date, func, select
from sqlalchemy import cast as sql_cast
from sqlalchemy.orm import Session

from healthcurve.analytics.wake_reference import DISPLAY_QUANTUM, free_from_total
from healthcurve.events import service as event_service
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminMetricEvent,
    GarminMetricType,
)

MODEL_ID: Final = "hc-exercise-response-v1"
MODEL_REVISION: Final = "hc-exercise-response-v1.0.0"
SERIES_UNIT: Final = "nmol/L"
CARRYOVER_HOURS: Final = 4
RESTING_FALLBACK_DAYS: Final = 14
MAX_HEART_RATE_LOOKBACK_DAYS: Final = 180
MINIMUM_HEART_RATE_RESERVE_BPM: Final = 20.0
MAX_INTENSITY: Final = 1.2
FRACTION_QUANTUM: Final = Decimal("0.0001")
MINUTE: Final = timedelta(minutes=1)
PERCENTILES: Final = ("p5", "p50", "p95")

REFERENCES: Final = (
    {
        "label": "Hill et al. (2008)",
        "citation": "Hill EE et al. J Endocrinol Invest. 2008;31:587-591.",
        "url": "https://doi.org/10.1007/BF03345606",
        "use": (
            "30 minutes of exercise at 40%, 60%, and 80% of maximal capacity: no rise, "
            "about +40%, and about +83% circulating cortisol; sets the threshold and slope"
        ),
    },
    {
        "label": "Swain and Leutholtz (1997)",
        "citation": "Swain DP, Leutholtz BC. Med Sci Sports Exerc. 1997;29:410-414.",
        "url": "https://doi.org/10.1097/00005768-199703000-00017",
        "use": "percent heart-rate reserve tracks percent of aerobic capacity reserve",
    },
    {
        "label": "Tanaka et al. (2001)",
        "citation": "Tanaka H, Monahan KD, Seals DR. J Am Coll Cardiol. 2001;37:153-156.",
        "url": "https://doi.org/10.1016/S0735-1097(00)01054-8",
        "use": "age-estimated maximum heart rate 208 - 0.7 x age, used as a floor",
    },
)


@dataclass(frozen=True, slots=True)
class ExerciseResponseParameters:
    """Population parameters; every value is published with each response."""

    intensity_threshold_hrr: float = 0.40
    increment_per_intensity: float = 2.075
    calibration_minutes: float = 30.0
    response_half_life_minutes: float = 75.0
    max_increment_fraction: float = 2.0
    sample_gap_factor: float = 2.0

    @property
    def elimination_rate_per_minute(self) -> float:
        return math.log(2.0) / self.response_half_life_minutes

    def calibrated_increment(self, intensity: float) -> float:
        """Healthy total-cortisol rise after `calibration_minutes` at this intensity."""
        return max(0.0, intensity - self.intensity_threshold_hrr) * self.increment_per_intensity

    def drive_per_minute(self, intensity: float) -> float:
        rate = self.elimination_rate_per_minute
        reached = 1.0 - math.exp(-rate * self.calibration_minutes)
        return self.calibrated_increment(intensity) * rate / reached


DEFAULT_PARAMETERS: Final = ExerciseResponseParameters()


@dataclass(frozen=True, slots=True)
class HeartRateSample:
    at: datetime
    bpm: float
    interval_seconds: int | None = None


@dataclass(frozen=True, slots=True)
class ActivityWindow:
    activity_id: str
    sport: str
    started_at: datetime
    ended_at: datetime


def age_estimated_max_heart_rate(age_years: float) -> float:
    return 208.0 - 0.7 * age_years


def _fraction(value: float) -> Decimal:
    return Decimal(str(value)).quantize(FRACTION_QUANTUM)


def _nmol(value: float) -> Decimal:
    return Decimal(str(value)).quantize(DISPLAY_QUANTUM)


def _model(parameters: ExerciseResponseParameters) -> dict[str, object]:
    return {
        "id": MODEL_ID,
        "revision": MODEL_REVISION,
        "parameters": {key: _fraction(value) for key, value in asdict(parameters).items()},
        "carryover_hours": CARRYOVER_HOURS,
        "references": [dict(reference) for reference in REFERENCES],
    }


def _unavailable(
    *, day: date, timezone: str, missing: list[str], parameters: ExerciseResponseParameters
) -> dict[str, object]:
    return {
        "available": False,
        "date": day,
        "timezone": timezone,
        "series_unit": SERIES_UNIT,
        "model": _model(parameters),
        "missing_inputs": missing,
        "inputs": None,
        "summary": None,
        "activities": [],
        "samples": [],
    }


def _intensity_by_minute(
    samples: Sequence[HeartRateSample],
    *,
    origin: datetime,
    minutes: int,
    resting_bpm: float,
    reserve_bpm: float,
    parameters: ExerciseResponseParameters,
) -> list[float | None]:
    """Each sample covers until the next one, but never across an unobserved gap."""
    intensity: list[float | None] = [None] * minutes
    ordered = sorted(samples, key=lambda sample: sample.at)
    deltas = [
        (right.at - left.at).total_seconds()
        for left, right in pairwise(ordered)
        if right.at > left.at
    ]
    typical = statistics.median(deltas) if deltas else 60.0
    for index, sample in enumerate(ordered):
        cadence = float(sample.interval_seconds or typical)
        covered_until = sample.at + timedelta(seconds=cadence)
        if index + 1 < len(ordered):
            following = ordered[index + 1].at
            if following - sample.at <= timedelta(seconds=cadence * parameters.sample_gap_factor):
                covered_until = following
        value = min(MAX_INTENSITY, max(0.0, (sample.bpm - resting_bpm) / reserve_bpm))
        first = max(0, math.floor((sample.at - origin) / MINUTE))
        last = min(minutes, math.ceil((covered_until - origin) / MINUTE))
        for minute in range(first, last):
            intensity[minute] = value
    return intensity


def _increments(
    intensity: Sequence[float | None], parameters: ExerciseResponseParameters
) -> list[float]:
    """Healthy total-cortisol increment fraction at the end of each minute."""
    rate = parameters.elimination_rate_per_minute
    decay = math.exp(-rate)
    increments: list[float] = []
    level = 0.0
    for value in intensity:
        drive = 0.0 if value is None else parameters.drive_per_minute(value)
        level = level * decay + drive / rate * (1.0 - decay)
        level = min(level, parameters.max_increment_fraction)
        increments.append(level)
    return increments


def build_response(
    *,
    day: date,
    timezone: str,
    reference: Mapping[str, object],
    heart_rate: Sequence[HeartRateSample],
    resting_heart_rate_bpm: float | None,
    resting_heart_rate_source: str | None,
    observed_peak_heart_rate_bpm: float | None,
    activities: Sequence[ActivityWindow] = (),
    parameters: ExerciseResponseParameters = DEFAULT_PARAMETERS,
) -> dict[str, object]:
    """Raise the healthy reference band for the heart-rate load of one local day."""
    zone = ZoneInfo(timezone)
    day_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    origin = day_start - timedelta(hours=CARRYOVER_HOURS)
    in_window = [sample for sample in heart_rate if origin <= sample.at < day_end]

    missing: list[str] = []
    if reference.get("available") is not True:
        missing.append("wake_reference")
    if resting_heart_rate_bpm is None:
        missing.append("resting_heart_rate")
    if not in_window:
        missing.append("heart_rate_samples")
    if missing or resting_heart_rate_bpm is None:
        return _unavailable(day=day, timezone=timezone, missing=missing, parameters=parameters)

    assumptions = cast(Mapping[str, object], reference["assumptions"])
    age_years = float(cast(Decimal, assumptions["age_years"]))
    estimated_max = age_estimated_max_heart_rate(age_years)
    observed_peak = observed_peak_heart_rate_bpm or 0.0
    max_heart_rate = max(estimated_max, observed_peak)
    reserve = max_heart_rate - resting_heart_rate_bpm
    if reserve < MINIMUM_HEART_RATE_RESERVE_BPM:
        return _unavailable(
            day=day, timezone=timezone, missing=["heart_rate_reserve"], parameters=parameters
        )

    minutes = math.ceil((day_end - origin) / MINUTE)
    intensity = _intensity_by_minute(
        in_window,
        origin=origin,
        minutes=minutes,
        resting_bpm=resting_heart_rate_bpm,
        reserve_bpm=reserve,
        parameters=parameters,
    )
    increments = _increments(intensity, parameters)
    day_first = math.floor((day_start - origin) / MINUTE)

    def minute_index(instant: datetime) -> int:
        return min(minutes - 1, max(0, math.floor((instant - origin) / MINUTE) - 1))

    samples: list[dict[str, object]] = []
    base_medians: list[tuple[datetime, float, float]] = []
    for reference_sample in cast(list[Mapping[str, object]], reference["samples"]):
        instant = cast(datetime, reference_sample["occurred_at"]).astimezone(UTC)
        index = minute_index(instant)
        increment = increments[index]
        sample: dict[str, object] = {
            "occurred_at": instant,
            "intensity_hrr": None
            if intensity[index] is None
            else _fraction(intensity[index] or 0.0),
            "increment_fraction": _fraction(increment),
        }
        for label in PERCENTILES:
            total = float(cast(Decimal, reference_sample[f"serum_total_{label}_nmol_l"]))
            sample[f"serum_free_{label}_nmol_l"] = _nmol(free_from_total(total * (1.0 + increment)))
        base_median = float(cast(Decimal, reference_sample["serum_free_p50_nmol_l"]))
        base_medians.append(
            (instant, base_median, float(cast(Decimal, sample["serum_free_p50_nmol_l"])))
        )
        samples.append(sample)

    extra_area = 0.0
    for (left_at, left_base, left_raised), (right_at, right_base, right_raised) in pairwise(
        base_medians
    ):
        hours = (right_at - left_at).total_seconds() / 3600.0
        extra_area += hours * ((left_raised - left_base) + (right_raised - right_base)) / 2.0

    day_intensity = intensity[day_first:]
    day_increments = increments[day_first:]
    peak_offset = max(range(len(day_increments)), key=day_increments.__getitem__)
    covered = sum(1 for value in day_intensity if value is not None)
    above = sum(
        1
        for value in day_intensity
        if value is not None and value > parameters.intensity_threshold_hrr
    )

    activity_rows: list[dict[str, object]] = []
    for activity in activities:
        started = max(activity.started_at.astimezone(UTC), day_start)
        ended = min(activity.ended_at.astimezone(UTC), day_end)
        if ended <= started:
            continue
        first = math.floor((started - origin) / MINUTE)
        last = math.ceil((ended - origin) / MINUTE)
        observed = [value for value in intensity[first:last] if value is not None]
        recovery_end = min(minutes, last + 60)
        activity_rows.append(
            {
                "activity_id": activity.activity_id,
                "sport": activity.sport,
                "started_at": activity.started_at,
                "ended_at": activity.ended_at,
                "duration_minutes": _fraction((ended - started).total_seconds() / 60.0),
                "observed_heart_rate_minutes": len(observed),
                "mean_intensity_hrr": _fraction(statistics.fmean(observed)) if observed else None,
                "minutes_above_threshold": sum(
                    1 for value in observed if value > parameters.intensity_threshold_hrr
                ),
                "peak_increment_fraction": _fraction(max(increments[first:recovery_end])),
            }
        )

    return {
        "available": True,
        "date": day,
        "timezone": timezone,
        "series_unit": SERIES_UNIT,
        "model": _model(parameters),
        "missing_inputs": [],
        "inputs": {
            "resting_heart_rate_bpm": _fraction(resting_heart_rate_bpm),
            "resting_heart_rate_source": resting_heart_rate_source,
            "max_heart_rate_bpm": _fraction(max_heart_rate),
            "max_heart_rate_source": (
                "observed_peak" if observed_peak > estimated_max else "age_estimate"
            ),
            "age_estimated_max_heart_rate_bpm": _fraction(estimated_max),
            "observed_peak_heart_rate_bpm": (
                None if observed_peak_heart_rate_bpm is None else _fraction(observed_peak)
            ),
            "age_years_assumption": _fraction(age_years),
            "heart_rate_sample_count": len(in_window),
            "heart_rate_observed_minutes": covered,
            "heart_rate_unobserved_minutes": len(day_intensity) - covered,
        },
        "summary": {
            "minutes_above_threshold": above,
            "peak_increment_fraction": _fraction(day_increments[peak_offset]),
            "peak_at": day_start + (peak_offset + 1) * MINUTE,
            "extra_median_free_nmol_l_hours": _nmol(max(0.0, extra_area)),
        },
        "activities": activity_rows,
        "samples": samples,
    }


def _current_metric_values(
    session: Session,
    *,
    owner_id: uuid.UUID,
    metric_type: GarminMetricType,
    aggregation: str,
    start: datetime,
    end: datetime,
) -> list[GarminMetricEvent]:
    return list(
        session.scalars(
            select(GarminMetricEvent)
            .where(
                GarminMetricEvent.owner_id == owner_id,
                GarminMetricEvent.metric_type == metric_type,
                GarminMetricEvent.aggregation == aggregation,
                GarminMetricEvent.occurred_at >= start,
                GarminMetricEvent.occurred_at < end,
                event_service.current_fact_predicate(GarminMetricEvent, owner_id=owner_id),
            )
            .order_by(GarminMetricEvent.occurred_at, GarminMetricEvent.id)
        )
    )


def _resting_heart_rate(
    session: Session, *, owner_id: uuid.UUID, day: date
) -> tuple[float | None, str | None]:
    rows = session.execute(
        select(sql_cast(GarminMetricEvent.local_time, Date), GarminMetricEvent.value).where(
            GarminMetricEvent.owner_id == owner_id,
            GarminMetricEvent.metric_type == GarminMetricType.RESTING_HEART_RATE,
            GarminMetricEvent.aggregation == "daily_summary",
            sql_cast(GarminMetricEvent.local_time, Date)
            >= day - timedelta(days=RESTING_FALLBACK_DAYS),
            sql_cast(GarminMetricEvent.local_time, Date) <= day,
            event_service.current_fact_predicate(GarminMetricEvent, owner_id=owner_id),
        )
    ).all()
    same_day = [float(value) for local_day, value in rows if local_day == day]
    if same_day:
        return same_day[0], "garmin_daily"
    prior = [float(value) for local_day, value in rows if local_day < day]
    if prior:
        return statistics.median(prior), "median_prior_14_days"
    return None, None


def response_for_owner(
    session: Session,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
    reference: Mapping[str, object],
    parameters: ExerciseResponseParameters = DEFAULT_PARAMETERS,
) -> dict[str, object]:
    """Load current Garmin facts for one local day and build the theoretical response."""
    zone = ZoneInfo(timezone)
    day_start = datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC)
    day_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)
    heart_rate = [
        HeartRateSample(
            at=row.occurred_at, bpm=float(row.value), interval_seconds=row.sample_interval_seconds
        )
        for row in _current_metric_values(
            session,
            owner_id=owner_id,
            metric_type=GarminMetricType.HEART_RATE,
            aggregation="provider_sample",
            start=day_start - timedelta(hours=CARRYOVER_HOURS),
            end=day_end,
        )
    ]
    resting, resting_source = _resting_heart_rate(session, owner_id=owner_id, day=day)
    observed_peak = session.scalar(
        select(func.max(GarminMetricEvent.value)).where(
            GarminMetricEvent.owner_id == owner_id,
            GarminMetricEvent.metric_type == GarminMetricType.HEART_RATE,
            GarminMetricEvent.aggregation == "provider_sample",
            GarminMetricEvent.occurred_at >= day_end - timedelta(days=MAX_HEART_RATE_LOOKBACK_DAYS),
            GarminMetricEvent.occurred_at < day_end,
            event_service.current_fact_predicate(GarminMetricEvent, owner_id=owner_id),
        )
    )
    activities = [
        ActivityWindow(
            activity_id=str(row.id),
            sport=row.sport,
            started_at=row.occurred_at,
            ended_at=row.ended_at,
        )
        for row in session.scalars(
            select(GarminActivityEvent)
            .where(
                GarminActivityEvent.owner_id == owner_id,
                GarminActivityEvent.occurred_at < day_end,
                GarminActivityEvent.ended_at > day_start,
                event_service.current_fact_predicate(GarminActivityEvent, owner_id=owner_id),
            )
            .order_by(GarminActivityEvent.occurred_at, GarminActivityEvent.id)
        )
    ]
    return build_response(
        day=day,
        timezone=timezone,
        reference=reference,
        heart_rate=heart_rate,
        resting_heart_rate_bpm=resting,
        resting_heart_rate_source=resting_source,
        observed_peak_heart_rate_bpm=None if observed_peak is None else float(observed_peak),
        activities=activities,
        parameters=parameters,
    )
