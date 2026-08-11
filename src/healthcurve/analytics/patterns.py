"""Comparable deterministic daily features from current recorded facts.

The projection is intentionally calculated on request. It is neither a recorded fact
nor an AI conclusion, and a correction/provider revision naturally changes both the
values and the source revision watermark returned to the owner.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from itertools import pairwise
from statistics import median
from typing import Final, TypedDict, cast
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from healthcurve.analytics import exposure
from healthcurve.episodes.models import StressEpisode
from healthcurve.events import service as event_service
from healthcurve.events.base import EventMixin
from healthcurve.events.models import SymptomEvent
from healthcurve.integrations.garmin.models import GarminMetricEvent, GarminMetricType
from healthcurve.medications.models import DoseEvent
from healthcurve.vitals.models import BloodPressureEvent

FEATURE_VERSION: Final = "hc-daily-pattern-v1"
MAX_RANGE_DAYS: Final = 366
DISPLAY_QUANTUM: Final = Decimal("0.0001")
EXPOSURE_QUANTUM: Final = Decimal("0.000000001")
METRICS: Final = (
    GarminMetricType.STRESS,
    GarminMetricType.HEART_RATE,
    GarminMetricType.HRV,
    GarminMetricType.RESPIRATION_RATE,
)
SAFETY_LABEL: Final = (
    "Descriptive daily features align current recorded facts with theoretical exposure. "
    "They do not establish causation, measure cortisol, diagnose a condition, or advise dosing."
)
DEFINITIONS: Final = {
    "exposure": (
        "Peak and trapezoidal area under hc-exposure-v1 for the local day. REU and "
        "REU-hours are theoretical relative units, not cortisol concentrations or coverage."
    ),
    "dose_counts": (
        "Supported and excluded dose counts include actual administrations within the local "
        "day. Earlier carryover doses can affect exposure and the source fingerprint but are "
        "not counted again as administrations."
    ),
    "symptom_timing": (
        "Each current symptom retains its recorded 0-10 severity. Timing is minutes since "
        "the latest supported actual-dose instant within the model's preceding 24-hour "
        "horizon; exposure is evaluated directly at the symptom instant."
    ),
    "wearable_ranges": (
        "Minimum, arithmetic average, and maximum use current Garmin provider samples in "
        "their native unit. Incompatible units are not combined."
    ),
    "wearable_coverage": (
        "Observed coverage is the union of sample intervals whose cadence Garmin supplied, "
        "clipped to the local day. Expected samples and missing counts are not invented; "
        "samples without cadence are reported separately."
    ),
    "blood_pressure": (
        "Ranges summarize current discrete systolic, diastolic, and optional pulse facts. "
        "A missing pulse is not converted to zero."
    ),
    "stress_episodes": (
        "Count and overlap duration include current recorded episode intervals intersecting "
        "the local day. Open intervals are clipped at the end of the selected day."
    ),
    "plan_versions": (
        "Plan version IDs are only those explicitly linked to actual doses administered on "
        "the day; an empty list does not mean that no physician-approved plan existed."
    ),
    "source_revision_watermark": (
        "SHA-256 over feature/model versions and the current contributing fact identities, "
        "revisions, timestamps, and structural values. It changes when a correction or "
        "provider revision changes the computed day; it contains no readable health values."
    ),
    "longitudinal_summary": (
        "Range distributions use one deterministic value per local day. A trend is the "
        "last observed daily value minus the first and is withheld until at least seven "
        "days contain that value. Observed-day coverage describes data availability only; "
        "it is not cortisol coverage, adequacy, or physiological demand."
    ),
}

MINIMUM_TREND_DAYS: Final = 7


class _ExposureSample(TypedDict):
    occurred_at: datetime
    local_time: datetime
    utc_offset_minutes: int
    theoretical_exposure_reu: Decimal


def _decimal_average(values: Sequence[Decimal]) -> Decimal | None:
    if not values:
        return None
    return (sum(values, Decimal(0)) / Decimal(len(values))).quantize(DISPLAY_QUANTUM)


def _range(values: Sequence[Decimal]) -> dict[str, Decimal | None]:
    return {
        "minimum": min(values) if values else None,
        "average": _decimal_average(values),
        "maximum": max(values) if values else None,
    }


def _observed_coverage(
    samples: Sequence[GarminMetricEvent], *, start: datetime, end: datetime
) -> tuple[Decimal, Decimal, int, str]:
    intervals: list[tuple[datetime, datetime]] = []
    missing_cadence = 0
    for sample in samples:
        if sample.sample_interval_seconds is None:
            missing_cadence += 1
            continue
        interval_start = max(start, sample.occurred_at.astimezone(UTC))
        interval_end = min(
            end,
            sample.occurred_at.astimezone(UTC) + timedelta(seconds=sample.sample_interval_seconds),
        )
        if interval_end > interval_start:
            intervals.append((interval_start, interval_end))
    intervals.sort()
    merged: list[list[datetime]] = []
    for interval_start, interval_end in intervals:
        if not merged or interval_start > merged[-1][1]:
            merged.append([interval_start, interval_end])
        else:
            merged[-1][1] = max(merged[-1][1], interval_end)
    seconds = sum(
        Decimal(str((interval_end - interval_start).total_seconds()))
        for interval_start, interval_end in merged
    )
    day_seconds = Decimal(str((end - start).total_seconds()))
    minutes = (seconds / Decimal(60)).quantize(DISPLAY_QUANTUM)
    percent = (
        (seconds * Decimal(100) / day_seconds).quantize(DISPLAY_QUANTUM)
        if day_seconds > 0
        else Decimal(0)
    )
    if not samples:
        state = "no_samples"
    elif not intervals:
        state = "cadence_unavailable"
    elif seconds >= day_seconds:
        state = "full_observed_coverage"
    else:
        state = "partial_observed_coverage"
    return minutes, percent, missing_cadence, state


def _wearable_feature(
    metric: GarminMetricType,
    samples: Sequence[GarminMetricEvent],
    *,
    start: datetime,
    end: datetime,
) -> dict[str, object]:
    units = sorted({sample.unit for sample in samples})
    compatible = len(units) <= 1
    values = [sample.value for sample in samples] if compatible else []
    coverage_minutes, coverage_percent, missing_cadence, state = _observed_coverage(
        samples, start=start, end=end
    )
    return {
        "metric_type": metric,
        "unit": units[0] if len(units) == 1 else None,
        "sample_count": len(samples),
        "samples_without_cadence": missing_cadence,
        "observed_coverage_minutes": coverage_minutes,
        "observed_coverage_percent": coverage_percent,
        "missingness_state": state,
        "incompatible_units": not compatible,
        **_range(values),
    }


def _event_token(kind: str, row: EventMixin, *values: object) -> str:
    structural = [
        kind,
        str(row.id),
        row.occurred_at.astimezone(UTC).isoformat(),
        row.recorded_at.astimezone(UTC).isoformat(),
        row.source_revision or "",
        str(row.supersedes_id or ""),
        *(str(value) for value in values),
    ]
    return "\x1f".join(structural)


def _watermark(tokens: Iterable[str]) -> str:
    body = json.dumps(sorted(tokens), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _longitudinal_metric(
    *, key: str, label: str, unit: str, values: Sequence[Decimal | None], total_days: int
) -> dict[str, object]:
    observed = [value for value in values if value is not None]
    observed_days = len(observed)
    eligible = observed_days >= MINIMUM_TREND_DAYS
    return {
        "key": key,
        "label": label,
        "unit": unit,
        "observed_days": observed_days,
        "missing_days": total_days - observed_days,
        "observed_day_percent": (
            (Decimal(observed_days) * Decimal(100) / Decimal(total_days)).quantize(DISPLAY_QUANTUM)
            if total_days
            else Decimal(0)
        ),
        "minimum": min(observed) if observed else None,
        "median": Decimal(str(median(observed))).quantize(DISPLAY_QUANTUM) if observed else None,
        "maximum": max(observed) if observed else None,
        "first_observed": observed[0] if observed else None,
        "last_observed": observed[-1] if observed else None,
        "first_to_last_change": (
            (observed[-1] - observed[0]).quantize(DISPLAY_QUANTUM) if eligible else None
        ),
        "trend_eligible": eligible,
    }


def _wearable_average(day: dict[str, object], metric: GarminMetricType) -> Decimal | None:
    for row in cast(list[dict[str, object]], day.get("wearables", [])):
        if row.get("metric_type") == metric:
            value = row.get("average")
            return value if isinstance(value, Decimal) else None
    return None


def _longitudinal_summary(days: list[dict[str, object]]) -> dict[str, object]:
    specs: list[tuple[str, str, str, list[Decimal | None]]] = [
        (
            "exposure_auc_reu_hours",
            "Theoretical exposure AUC",
            "REU-hours",
            [cast(Decimal | None, day.get("exposure_auc_reu_hours")) for day in days],
        ),
        (
            "average_symptom_severity",
            "Average recorded symptom severity",
            "0-10",
            [cast(Decimal | None, day.get("average_symptom_severity")) for day in days],
        ),
    ]
    labels = {
        GarminMetricType.STRESS: ("garmin_stress_average", "Garmin stress daily average", "score"),
        GarminMetricType.HEART_RATE: ("heart_rate_average", "Heart rate daily average", "bpm"),
        GarminMetricType.HRV: ("hrv_average", "HRV daily average", "ms"),
        GarminMetricType.RESPIRATION_RATE: (
            "respiration_rate_average",
            "Respiration daily average",
            "breaths/min",
        ),
    }
    for metric, (key, label, unit) in labels.items():
        specs.append((key, label, unit, [_wearable_average(day, metric) for day in days]))

    boundaries: list[dict[str, object]] = []
    for day in days:
        signature = (day.get("feature_version", FEATURE_VERSION), day.get("exposure_model_version"))
        if (
            boundaries
            and boundaries[-1]["feature_version"] == signature[0]
            and boundaries[-1]["exposure_model_version"] == signature[1]
        ):
            boundaries[-1]["date_to"] = day["date"]
        else:
            boundaries.append(
                {
                    "date_from": day["date"],
                    "date_to": day["date"],
                    "feature_version": signature[0],
                    "exposure_model_version": signature[1],
                }
            )
    return {
        "total_days": len(days),
        "minimum_observed_days_for_trend": MINIMUM_TREND_DAYS,
        "coverage_definition": (
            "Observed-day coverage is the share of selected local days with a recorded value. "
            "It does not measure cortisol sufficiency or physiological need."
        ),
        "multiple_comparison_caution": (
            "Reviewing many metrics and date ranges can surface chance patterns. These are "
            "descriptive comparisons; correlation or association does not establish causation "
            "or diagnosis."
        ),
        "metrics": [
            _longitudinal_metric(
                key=key, label=label, unit=unit, values=values, total_days=len(days)
            )
            for key, label, unit, values in specs
        ],
        "model_version_periods": boundaries,
    }


def _exposure_auc(samples: Sequence[_ExposureSample]) -> Decimal:
    area = Decimal(0)
    for left, right in pairwise(samples):
        elapsed_hours = Decimal(
            str(
                (
                    right["occurred_at"].astimezone(UTC) - left["occurred_at"].astimezone(UTC)
                ).total_seconds()
                / 3600
            )
        )
        area += (
            (left["theoretical_exposure_reu"] + right["theoretical_exposure_reu"])
            / Decimal(2)
            * elapsed_hours
        )
    return area.quantize(EXPOSURE_QUANTUM)


def _exposure_dose(row: DoseEvent) -> exposure.ExposureDose:
    return exposure.ExposureDose(
        id=row.id,
        occurred_at=row.occurred_at,
        local_time=row.local_time,
        timezone=row.timezone,
        utc_offset_minutes=row.utc_offset_minutes,
        amount=row.amount,
        unit=row.unit,
        route=row.route,
        medication_name=row.medication.name,
        normalized_medication_name=row.medication.normalized_name,
        formulation=row.medication.formulation,
        source_type=row.source_type.value,
        confirmation_state=row.confirmation_state.value,
        supersedes_id=row.supersedes_id,
    )


def _facts_in_window[E: EventMixin](
    rows: Sequence[E], *, start: datetime, end: datetime
) -> list[E]:
    return [row for row in rows if start <= row.occurred_at.astimezone(UTC) < end]


def _day_feature(
    *,
    day: date,
    timezone: str,
    doses: Sequence[DoseEvent],
    symptoms: Sequence[SymptomEvent],
    garmin: Sequence[GarminMetricEvent],
    blood_pressure: Sequence[BloodPressureEvent],
    episodes: Sequence[StressEpisode],
) -> dict[str, object]:
    zone = ZoneInfo(timezone)
    local_start = datetime.combine(day, time.min, tzinfo=zone)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    start = local_start.astimezone(UTC)
    end = local_end.astimezone(UTC)
    relevant_doses = [
        row
        for row in doses
        if start - timedelta(hours=exposure.HORIZON_HOURS) <= row.occurred_at.astimezone(UTC) < end
    ]
    exposure_doses = [_exposure_dose(row) for row in relevant_doses]
    supported = [dose for dose in exposure_doses if exposure.exclusion_reason(dose) is None]
    curve = exposure.build_curve(day=day, timezone=timezone, doses=exposure_doses)
    samples_value = curve["samples"]
    if not isinstance(samples_value, list):  # pragma: no cover - internal invariant
        raise TypeError("exposure samples must be a list")
    samples = cast(list[_ExposureSample], samples_value)
    model = cast(dict[str, object], curve["model"])
    model_version = str(model["version"])
    peak_sample = max(
        samples,
        key=lambda sample: (sample["theoretical_exposure_reu"], -sample["occurred_at"].timestamp()),
    )
    day_doses = _facts_in_window(relevant_doses, start=start, end=end)
    day_symptoms = _facts_in_window(symptoms, start=start, end=end)
    day_garmin = _facts_in_window(garmin, start=start, end=end)
    day_blood_pressure = _facts_in_window(blood_pressure, start=start, end=end)

    symptom_timings = []
    severities: list[Decimal] = []
    for symptom in sorted(day_symptoms, key=lambda row: (row.occurred_at, row.id)):
        if symptom.severity is not None:
            severities.append(Decimal(symptom.severity))
        previous = [
            dose
            for dose in supported
            if dose.occurred_at.astimezone(UTC) <= symptom.occurred_at.astimezone(UTC)
        ]
        latest_at = max((dose.occurred_at for dose in previous), default=None)
        latest = (
            [dose for dose in previous if dose.occurred_at == latest_at]
            if latest_at is not None
            else []
        )
        minutes_since = (
            Decimal(
                str(
                    (
                        symptom.occurred_at.astimezone(UTC) - latest_at.astimezone(UTC)
                    ).total_seconds()
                    / 60
                )
            ).quantize(DISPLAY_QUANTUM)
            if latest_at is not None
            else None
        )
        symptom_timings.append(
            {
                "symptom_event_id": symptom.id,
                "occurred_at": symptom.occurred_at.astimezone(UTC),
                "name": symptom.name,
                "severity": symptom.severity,
                "previous_supported_dose_event_ids": sorted((dose.id for dose in latest), key=str),
                "minutes_since_previous_supported_dose": minutes_since,
                "theoretical_exposure_reu": exposure.exposure_reu_at(
                    supported, symptom.occurred_at
                ),
            }
        )

    wearable_features = [
        _wearable_feature(
            metric,
            [row for row in day_garmin if row.metric_type == metric],
            start=start,
            end=end,
        )
        for metric in METRICS
    ]
    systolic = [Decimal(row.systolic_mmhg) for row in day_blood_pressure]
    diastolic = [Decimal(row.diastolic_mmhg) for row in day_blood_pressure]
    pulse = [Decimal(row.pulse_bpm) for row in day_blood_pressure if row.pulse_bpm is not None]

    overlapping = []
    overlap_minutes = Decimal(0)
    for episode in episodes:
        episode_start = max(start, episode.started_at.astimezone(UTC))
        episode_end = min(end, (episode.ended_at or end).astimezone(UTC))
        if episode_end <= episode_start:
            continue
        overlapping.append(episode)
        overlap_minutes += Decimal(str((episode_end - episode_start).total_seconds() / 60))

    tokens = [
        f"feature:{FEATURE_VERSION}",
        f"model:{model_version}",
        f"date:{day.isoformat()}",
        f"timezone:{timezone}",
    ]
    tokens.extend(
        _event_token(
            "dose",
            row,
            row.amount,
            row.unit.value,
            row.route.value,
            row.medication_id,
            row.regimen_version_id,
        )
        for row in relevant_doses
    )
    tokens.extend(_event_token("symptom", row, row.name, row.severity) for row in day_symptoms)
    tokens.extend(
        _event_token(
            "garmin",
            row,
            row.metric_type.value,
            row.value,
            row.unit,
            row.sample_interval_seconds,
        )
        for row in day_garmin
    )
    tokens.extend(
        _event_token(
            "blood_pressure",
            row,
            row.systolic_mmhg,
            row.diastolic_mmhg,
            row.pulse_bpm,
        )
        for row in day_blood_pressure
    )
    tokens.extend(
        "\x1f".join(
            (
                "episode",
                str(row.id),
                row.recorded_at.astimezone(UTC).isoformat(),
                row.started_at.astimezone(UTC).isoformat(),
                row.ended_at.astimezone(UTC).isoformat() if row.ended_at else "",
                row.status.value,
                row.severity.value if row.severity else "",
            )
        )
        for row in overlapping
    )
    return {
        "date": day,
        "timezone": timezone,
        "elapsed_hours": Decimal(str((end - start).total_seconds() / 3600)),
        "feature_version": FEATURE_VERSION,
        "exposure_model_version": model_version,
        "dose_plan_version_ids": sorted(
            {row.regimen_version_id for row in day_doses if row.regimen_version_id is not None},
            key=str,
        ),
        "source_revision_watermark_sha256": _watermark(tokens),
        "supported_dose_count": sum(
            exposure.exclusion_reason(_exposure_dose(row)) is None for row in day_doses
        ),
        "excluded_dose_count": sum(
            exposure.exclusion_reason(_exposure_dose(row)) is not None for row in day_doses
        ),
        "exposure_peak_reu": peak_sample["theoretical_exposure_reu"],
        "exposure_peak_at": peak_sample["occurred_at"],
        "exposure_auc_reu_hours": _exposure_auc(samples),
        "symptom_count": len(day_symptoms),
        "symptom_severity_sample_count": len(severities),
        "symptom_severity_missing_count": len(day_symptoms) - len(severities),
        "average_symptom_severity": _decimal_average(severities),
        "symptom_timings": symptom_timings,
        "wearables": wearable_features,
        "blood_pressure": {
            "sample_count": len(day_blood_pressure),
            "pulse_sample_count": len(pulse),
            "pulse_missing_count": len(day_blood_pressure) - len(pulse),
            "systolic": _range(systolic),
            "diastolic": _range(diastolic),
            "pulse": _range(pulse),
        },
        "stress_episodes": {
            "count": len(overlapping),
            "open_count": sum(row.ended_at is None for row in overlapping),
            "overlap_minutes": overlap_minutes.quantize(DISPLAY_QUANTUM),
        },
    }


def daily_patterns_for_owner(
    session: Session,
    *,
    owner_id: uuid.UUID,
    date_from: date,
    date_to: date,
    timezone: str,
) -> dict[str, object]:
    """Calculate bounded, comparable features from the latest fact revisions."""
    zone = ZoneInfo(timezone)
    start = datetime.combine(date_from, time.min, tzinfo=zone).astimezone(UTC)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC)

    dose_rows = list(
        session.scalars(
            select(DoseEvent)
            .where(
                DoseEvent.owner_id == owner_id,
                DoseEvent.occurred_at >= start - timedelta(hours=exposure.HORIZON_HOURS),
                DoseEvent.occurred_at < end,
            )
            .order_by(DoseEvent.occurred_at, DoseEvent.id)
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
    garmin_rows = list(
        session.scalars(
            select(GarminMetricEvent).where(
                GarminMetricEvent.owner_id == owner_id,
                GarminMetricEvent.aggregation == "provider_sample",
                GarminMetricEvent.metric_type.in_(METRICS),
                GarminMetricEvent.occurred_at >= start,
                GarminMetricEvent.occurred_at < end,
            )
        )
    )
    blood_pressure_rows = list(
        session.scalars(
            select(BloodPressureEvent).where(
                BloodPressureEvent.owner_id == owner_id,
                BloodPressureEvent.occurred_at >= start,
                BloodPressureEvent.occurred_at < end,
            )
        )
    )
    episode_rows = list(
        session.scalars(
            select(StressEpisode).where(
                StressEpisode.owner_id == owner_id,
                StressEpisode.started_at < end,
                or_(StressEpisode.ended_at.is_(None), StressEpisode.ended_at > start),
            )
        )
    )

    dose_rows = event_service.current_only(session, DoseEvent, dose_rows)
    symptom_rows = event_service.current_only(session, SymptomEvent, symptom_rows)
    garmin_rows = event_service.current_only(session, GarminMetricEvent, garmin_rows)
    blood_pressure_rows = event_service.current_only(
        session, BloodPressureEvent, blood_pressure_rows
    )

    days = []
    cursor = date_from
    while cursor <= date_to:
        days.append(
            _day_feature(
                day=cursor,
                timezone=timezone,
                doses=dose_rows,
                symptoms=symptom_rows,
                garmin=garmin_rows,
                blood_pressure=blood_pressure_rows,
                episodes=episode_rows,
            )
        )
        cursor += timedelta(days=1)
    return build_response(
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        days=days,
    )


def build_response(
    *, date_from: date, date_to: date, timezone: str, days: list[dict[str, object]]
) -> dict[str, object]:
    """Assemble a range without assuming one plan or model version across its days."""
    return {
        "date_from": date_from,
        "date_to": date_to,
        "timezone": timezone,
        "feature_version": FEATURE_VERSION,
        "safety_label": SAFETY_LABEL,
        "definitions": DEFINITIONS,
        "exposure_model_versions": sorted({str(day["exposure_model_version"]) for day in days}),
        "longitudinal_summary": _longitudinal_summary(days),
        "days": days,
    }
