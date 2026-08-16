"""Deterministic selected-day projection for private-model HealthCurve analysis.

The projection is calculated from current facts and approved plans on every request.
Dense intraday series are reduced into fixed local-time buckets so every sample
contributes without sending thousands of near-identical points to the model.  This
module never writes facts, plans, or AI output.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from enum import Enum
from typing import Any, Final, cast
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from healthcurve.analytics import exposure, patterns
from healthcurve.context.models import ContextEvent, LocationPrecision
from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events import service as event_service
from healthcurve.events.base import EventMixin
from healthcurve.events.models import DiaryEvent, LifeEvent, MealEvent, SymptomEvent
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminMetricEvent,
    GarminSleepEvent,
)
from healthcurve.labs.models import LabPanel
from healthcurve.medications import service as medication_service
from healthcurve.medications.models import DoseEvent, RegimenVersion
from healthcurve.vitals import service as vitals
from healthcurve.vitals.models import BloodPressureEvent, TemperatureEvent, WeightEvent

PROJECTION_VERSION: Final = "hc-day-analysis-v1"
MODEL_INPUT_VERSION: Final = "hc-day-model-input-v1"
BUCKET_MINUTES: Final = 15

_GARMIN_BUCKET_COLUMNS: Final = (
    "metric_type",
    "unit",
    "bucket_start_local",
    "sample_count",
    "minimum",
    "average",
    "maximum",
)
_EXPOSURE_BUCKET_COLUMNS: Final = (
    "bucket_start_local",
    "sample_count",
    "minimum_reu",
    "average_reu",
    "maximum_reu",
)


def _json_default(value: object) -> object:
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    raise TypeError(f"unsupported projection value: {type(value).__name__}")


def _jsonable(value: object) -> Any:
    return json.loads(
        json.dumps(value, default=_json_default, sort_keys=True, separators=(",", ":"))
    )


def _columnar_rows(rows: list[dict[str, object]], columns: tuple[str, ...]) -> dict[str, object]:
    """State repeated bucket keys once without dropping any measured aggregate."""
    return {
        "encoding": "columnar_rows_v1",
        "columns": list(columns),
        "rows": [[row[column] for column in columns] for row in rows],
    }


def build_model_inputs(projection: dict[str, object]) -> dict[str, object]:
    """Return a lossless, compact model view of the canonical day projection.

    The canonical projection remains the source-revision input. Only the two dense,
    homogeneous bucket arrays become columnar for inference, so every original sample
    still contributes through its count/minimum/average/maximum while repeated JSON
    keys and redundant hour/minute fields do not consume model context.
    """
    inputs = cast(
        dict[str, object],
        _jsonable({key: value for key, value in projection.items() if key != "source_record_ids"}),
    )
    facts = cast(dict[str, object], inputs["recorded_facts_and_plan_context"])
    garmin_rows = cast(list[dict[str, object]], facts["garmin_intraday_15_minute_buckets"])
    exposure_rows = cast(list[dict[str, object]], inputs["theoretical_exposure_15_minute_buckets"])
    facts["garmin_intraday_15_minute_buckets"] = _columnar_rows(garmin_rows, _GARMIN_BUCKET_COLUMNS)
    inputs["theoretical_exposure_15_minute_buckets"] = _columnar_rows(
        exposure_rows, _EXPOSURE_BUCKET_COLUMNS
    )
    inputs["model_input_version"] = MODEL_INPUT_VERSION
    return inputs


def _current_events[E: EventMixin](
    session: Session,
    model: type[E],
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[E]:
    return list(
        session.scalars(
            select(model)
            .where(
                model.owner_id == owner_id,
                model.occurred_at >= start,
                model.occurred_at < end,
                event_service.current_fact_predicate(model, owner_id=owner_id),
            )
            .order_by(model.occurred_at, model.id)
        )
    )


def _event_time(row: EventMixin, zone: ZoneInfo) -> dict[str, object]:
    local = row.occurred_at.astimezone(zone)
    return {
        "id": row.id,
        "occurred_at": row.occurred_at.astimezone(UTC),
        "local_time": local,
        "local_hour": local.hour,
        "local_minute": local.minute,
        "timezone": zone.key,
    }


def _dense_buckets(rows: list[GarminMetricEvent], zone: ZoneInfo) -> list[dict[str, object]]:
    grouped: dict[tuple[str, str, datetime], list[GarminMetricEvent]] = defaultdict(list)
    for row in rows:
        local = row.occurred_at.astimezone(zone)
        minute = local.minute - (local.minute % BUCKET_MINUTES)
        bucket = local.replace(minute=minute, second=0, microsecond=0)
        grouped[(row.metric_type.value, row.unit, bucket)].append(row)
    result: list[dict[str, object]] = []
    for (metric, unit, bucket), samples in sorted(grouped.items(), key=lambda item: item[0]):
        values = [sample.value for sample in samples]
        result.append(
            {
                "metric_type": metric,
                "unit": unit,
                "bucket_start_local": bucket,
                "local_hour": bucket.hour,
                "local_minute": bucket.minute,
                "sample_count": len(samples),
                "minimum": min(values),
                "average": sum(values, Decimal(0)) / Decimal(len(values)),
                "maximum": max(values),
            }
        )
    return result


def _exposure_buckets(curve: dict[str, object], zone: ZoneInfo) -> list[dict[str, object]]:
    grouped: dict[datetime, list[Decimal]] = defaultdict(list)
    samples = cast(list[dict[str, object]], curve["samples"])
    for sample in samples:
        occurred_at = cast(datetime, sample["occurred_at"])
        local = occurred_at.astimezone(zone)
        minute = local.minute - (local.minute % BUCKET_MINUTES)
        grouped[local.replace(minute=minute, second=0, microsecond=0)].append(
            cast(Decimal, sample["theoretical_exposure_reu"])
        )
    return [
        {
            "bucket_start_local": bucket,
            "local_hour": bucket.hour,
            "local_minute": bucket.minute,
            "sample_count": len(values),
            "minimum_reu": min(values),
            "average_reu": sum(values, Decimal(0)) / Decimal(len(values)),
            "maximum_reu": max(values),
        }
        for bucket, values in sorted(grouped.items())
    ]


def _plan(version: RegimenVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "category": "physician_approved_plan",
        "version_label": version.version_label,
        "effective_from": version.effective_from,
        "effective_to": version.effective_to,
        "effective_timezone": version.effective_timezone,
        "slots": [
            {
                "id": slot.id,
                "medication_name": slot.medication.name,
                "scheduled_local_time": slot.scheduled_local_time,
                "amount": slot.amount,
                "unit": slot.unit,
                "route": slot.route,
                "condition": slot.condition,
            }
            for slot in version.slots
        ],
        "instructions": [
            {
                "id": instruction.id,
                "category": instruction.category,
                "title": instruction.title,
                "body": instruction.body,
                "authored_on": instruction.authored_on,
            }
            for instruction in version.instructions
        ],
    }


def build_projection(
    session: Session, *, owner_id: uuid.UUID, day: date, timezone: str
) -> dict[str, object]:
    """Return a complete, JSON-safe, fingerprinted view of one selected local day."""
    zone = ZoneInfo(timezone)
    local_start = datetime.combine(day, time.min, tzinfo=zone)
    local_end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    start = local_start.astimezone(UTC)
    end = local_end.astimezone(UTC)

    daily_features = patterns.daily_patterns_for_owner(
        session,
        owner_id=owner_id,
        date_from=day,
        date_to=day,
        timezone=timezone,
    )["days"][0]  # type: ignore[index]
    curve = exposure.curve_for_owner(session, owner_id=owner_id, day=day, timezone=timezone)

    doses = _current_events(session, DoseEvent, owner_id=owner_id, start=start, end=end)
    symptoms = _current_events(session, SymptomEvent, owner_id=owner_id, start=start, end=end)
    meals = _current_events(session, MealEvent, owner_id=owner_id, start=start, end=end)
    injections = _current_events(
        session, EmergencyInjectionEvent, owner_id=owner_id, start=start, end=end
    )
    blood_pressure = _current_events(
        session, BloodPressureEvent, owner_id=owner_id, start=start, end=end
    )
    weights = _current_events(session, WeightEvent, owner_id=owner_id, start=start, end=end)
    temperatures = _current_events(
        session, TemperatureEvent, owner_id=owner_id, start=start, end=end
    )
    diary = _current_events(session, DiaryEvent, owner_id=owner_id, start=start, end=end)
    life_events = _current_events(session, LifeEvent, owner_id=owner_id, start=start, end=end)
    contexts = _current_events(session, ContextEvent, owner_id=owner_id, start=start, end=end)
    lab_panels = _current_events(session, LabPanel, owner_id=owner_id, start=start, end=end)
    garmin_metrics = _current_events(
        session, GarminMetricEvent, owner_id=owner_id, start=start, end=end
    )
    activities = _current_events(
        session, GarminActivityEvent, owner_id=owner_id, start=start, end=end
    )

    sleep_rows = list(
        session.scalars(
            select(GarminSleepEvent)
            .where(
                GarminSleepEvent.owner_id == owner_id,
                GarminSleepEvent.occurred_at < end,
                GarminSleepEvent.ended_at > start,
                event_service.current_fact_predicate(GarminSleepEvent, owner_id=owner_id),
            )
            .order_by(GarminSleepEvent.occurred_at, GarminSleepEvent.id)
        )
    )
    sleeps = sleep_rows
    episodes = list(
        session.scalars(
            select(StressEpisode)
            .where(
                StressEpisode.owner_id == owner_id,
                StressEpisode.started_at < end,
                or_(StressEpisode.ended_at.is_(None), StressEpisode.ended_at > start),
            )
            .order_by(StressEpisode.started_at, StressEpisode.id)
        )
    )
    plans = medication_service.approved_versions_during(
        session, owner_id=owner_id, start=start, end=end
    )

    provider_samples = [row for row in garmin_metrics if row.aggregation == "provider_sample"]
    daily_garmin = [row for row in garmin_metrics if row.aggregation != "provider_sample"]
    facts: dict[str, object] = {
        "doses": [
            {
                **_event_time(row, zone),
                "medication_name": row.medication.name,
                "amount": row.amount,
                "unit": row.unit,
                "route": row.route,
                "dose_category": row.category,
                "linked_plan_version_id": row.regimen_version_id,
                "linked_stress_episode_id": row.episode_id,
            }
            for row in doses
        ],
        "symptoms": [
            {
                **_event_time(row, zone),
                "name": row.name,
                "severity_0_to_10": row.severity,
                "body_area": row.body_area,
                "tracking_category": row.tracking_category,
                "tracking_category_revision": row.tracking_category_revision,
                "ended_at": row.ended_at,
                "notes": row.notes,
            }
            for row in symptoms
        ],
        "meals": [
            {
                **_event_time(row, zone),
                "size": row.size,
                "notes": row.notes,
            }
            for row in meals
        ],
        "stress_episodes": [
            {
                "id": row.id,
                "trigger": row.trigger,
                "severity": row.severity,
                "status": row.status,
                "started_at": row.started_at,
                "ended_at": row.ended_at,
                "highest_temperature_c": row.highest_temperature_c,
                "illness_description": row.illness_description,
                "recovery_notes": row.recovery_notes,
                "outcome": row.outcome,
                "notes": row.notes,
            }
            for row in episodes
        ],
        "emergency_injections": [
            {
                **_event_time(row, zone),
                "amount": row.amount,
                "unit": row.unit,
                "route": row.route,
                "reason": row.reason,
                "response": row.response,
                "emergency_services_called": row.emergency_services_called,
                "transported_to_hospital": row.transported_to_hospital,
            }
            for row in injections
        ],
        "blood_pressure": [
            {
                **_event_time(row, zone),
                "systolic_mmhg": row.systolic_mmhg,
                "diastolic_mmhg": row.diastolic_mmhg,
                "pulse_bpm": row.pulse_bpm,
                "measurement_setting": row.measurement_setting,
                "body_position": row.body_position,
            }
            for row in blood_pressure
        ],
        "weight": [
            {
                **_event_time(row, zone),
                "value": row.value,
                "unit": row.unit,
                "normalized_kg": row.normalized_kg,
                "measurement_setting": row.measurement_setting,
            }
            for row in weights
        ],
        "temperature": [
            {
                **_event_time(row, zone),
                "entered_value": row.value,
                "entered_unit": row.unit,
                "fahrenheit": vitals.display_temperature_f(row.value, row.unit),
                "celsius": vitals.display_temperature_c(row.value, row.unit),
            }
            for row in temperatures
        ],
        "diary": [
            {
                **_event_time(row, zone),
                "text": row.text,
                "tags": row.tags,
                "is_sensitive": row.is_sensitive,
            }
            for row in diary
        ],
        "life_events": [
            {
                **_event_time(row, zone),
                "title": row.title,
                "category": row.category,
                "description": row.description,
                "ended_at": row.ended_at,
                "is_sensitive": row.is_sensitive,
            }
            for row in life_events
        ],
        "labs": [
            {
                **_event_time(panel, zone),
                "laboratory_name": panel.laboratory_name,
                "specimen_type": panel.specimen_type,
                "report_status": panel.report_status,
                "results": [
                    {
                        "id": result.id,
                        "analyte_name": result.analyte_name,
                        "original_value": result.original_value,
                        "qualitative_result": result.qualitative_result,
                        "original_unit": result.original_unit,
                        "original_reference_range": result.original_reference_range,
                        "abnormal_flag": result.abnormal_flag,
                    }
                    for result in panel.results
                ],
            }
            for panel in lab_panels
        ],
        "garmin_intraday_15_minute_buckets": _dense_buckets(provider_samples, zone),
        "garmin_daily_or_point_metrics": [
            {
                **_event_time(row, zone),
                "metric_type": row.metric_type,
                "value": row.value,
                "unit": row.unit,
                "aggregation": row.aggregation,
            }
            for row in daily_garmin
        ],
        "garmin_sleep": [
            {
                **_event_time(row, zone),
                "ended_at": row.ended_at,
                "duration_seconds": row.duration_seconds,
                "overall_sleep_score": row.overall_sleep_score,
                "awakenings": row.awakenings,
                "awake_intervals": [
                    {"started_at": stage.started_at, "ended_at": stage.ended_at}
                    for stage in row.stage_intervals
                ],
            }
            for row in sleeps
        ],
        "garmin_activities": [
            {
                **_event_time(row, zone),
                "ended_at": row.ended_at,
                "sport": row.sport,
                "title": row.title,
                "elapsed_seconds": row.elapsed_seconds,
                "distance_miles": row.distance_miles,
                "calories": row.calories,
                "average_heart_rate": row.average_heart_rate,
                "maximum_heart_rate": row.maximum_heart_rate,
            }
            for row in activities
        ],
        "context_without_exact_coordinates": [
            {
                **_event_time(row, zone),
                "location": (
                    row.coarse_location_label
                    if row.location_precision is LocationPrecision.COARSE
                    else "exact location withheld from AI"
                    if row.location_precision is LocationPrecision.EXACT
                    else None
                ),
                "temperature": row.temperature,
                "temperature_unit": row.temperature_unit,
                "pressure": row.pressure,
                "pressure_unit": row.pressure_unit,
                "humidity_percent": row.humidity_percent,
                "precipitation": row.precipitation,
                "precipitation_unit": row.precipitation_unit,
                "conditions": row.conditions,
            }
            for row in contexts
        ],
        "physician_approved_plans": [_plan(version) for version in plans],
    }

    availability = {
        "doses": len(doses),
        "symptoms": len(symptoms),
        "meals": len(meals),
        "stress_episodes": len(episodes),
        "emergency_injections": len(injections),
        "blood_pressure": len(blood_pressure),
        "weight": len(weights),
        "temperature": len(temperatures),
        "diary": len(diary),
        "life_events": len(life_events),
        "labs": len(lab_panels),
        "garmin_intraday_samples": len(provider_samples),
        "garmin_daily_or_point_metrics": len(daily_garmin),
        "garmin_sleep": len(sleeps),
        "garmin_activities": len(activities),
        "context": len(contexts),
        "physician_approved_plans": len(plans),
    }
    missing_domains = [name for name, count in availability.items() if count == 0]
    source_ids = {
        str(row.id)
        for rows in (
            doses,
            symptoms,
            meals,
            injections,
            blood_pressure,
            weights,
            temperatures,
            diary,
            life_events,
            contexts,
            lab_panels,
            garmin_metrics,
            activities,
            sleeps,
            episodes,
        )
        for row in rows
    }
    source_ids.update(str(result.id) for panel in lab_panels for result in panel.results)
    source_ids.update(str(stage.id) for sleep in sleeps for stage in sleep.stage_intervals)
    source_ids.update(
        str(item_id)
        for version in plans
        for item_id in (
            version.id,
            *(slot.id for slot in version.slots),
            *(instruction.id for instruction in version.instructions),
        )
    )
    source_ids.update(
        str(marker["dose_event_id"])
        for marker in cast(list[dict[str, object]], curve["dose_markers"])
    )
    core = {
        "projection_version": PROJECTION_VERSION,
        "selected_local_date": day,
        "selected_timezone": timezone,
        "local_day_elapsed_hours": Decimal(str((end - start).total_seconds() / 3600)),
        "purpose": (
            "Describe temporal associations and questions for review; do not infer causation, "
            "diagnose, measure cortisol, determine medication need, or advise dosing."
        ),
        "data_availability_counts": availability,
        "missing_domains": missing_domains,
        "deterministic_daily_features": daily_features,
        "theoretical_exposure_15_minute_buckets": _exposure_buckets(curve, zone),
        "recorded_facts_and_plan_context": facts,
    }
    json_core = _jsonable(core)
    fingerprint = hashlib.sha256(
        json.dumps(json_core, sort_keys=True, separators=(",", ":")).encode("utf-8")
    ).hexdigest()
    return {
        **json_core,
        "source_revision_sha256": fingerprint,
        "source_record_id": f"healthcurve-day:{day.isoformat()}:{fingerprint}",
        "source_record_ids": sorted(source_ids),
    }
