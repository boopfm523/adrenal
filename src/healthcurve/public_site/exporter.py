"""Generate the public static curve projection without exposing private records.

The output of this module is deliberately a different contract from the authenticated
API. Every key is copied through an explicit allow-list, and all rendering keys are
new per-day opaque labels rather than database or provider identifiers.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import uuid
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
from tempfile import mkdtemp
from typing import Any, Final, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.api.pagination import PageRequest
from healthcurve.api.routers import analytics, episodes, events, garmin, vitals
from healthcurve.api.schemas import WakeFreeCortisolCurveOut
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.models import (
    GarminSyncRun,
    GarminSyncStatus,
)

PUBLIC_SCHEMA_VERSION: Final = "healthcurve-public-v1"
PUBLIC_MODEL: Final = "hc-mixed-route-free-v4"
PUBLIC_ACTIVITY_START_DATE: Final = date(2026, 8, 28)
SUCCESSFUL_SYNC_STATUSES: Final = frozenset(
    {GarminSyncStatus.COMPLETED, GarminSyncStatus.COMPLETED_WITH_WARNINGS}
)

SUPPORTED_ACTIVITY_TYPES: Final = frozenset(
    {
        "walking",
        "indoor_walking",
        "treadmill_walking",
        "running",
        "indoor_running",
        "treadmill_running",
        "treadmill",
        "rowing",
        "indoor_rowing",
        "rowing_machine",
        "indoor_rowing_machine",
    }
)

Projection = Mapping[str, "Projection | Literal[True]"]


PUBLIC_CURVE_PROJECTION: Final[Projection] = {
    "date": True,
    "timezone": True,
    "day_start": True,
    "day_end": True,
    "elapsed_hours": True,
    "series_kind": True,
    "series_name": True,
    "series_unit": True,
    "safety_label": True,
    "definition": True,
    "model": {
        "id": True,
        "revision": True,
        "supported_medication": True,
        "supported_formulation": True,
        "supported_route": True,
        "supported_medications": True,
        "supported_formulations": True,
        "supported_routes": True,
        "amount_unit": True,
        "binding_revision": True,
        "calibration_revision": True,
        "parameters": {
            "revision_number": True,
            "population_default": True,
            "source_revision": True,
            "elimination_half_life_hours": True,
            "peak_time_hours": True,
            "distribution_volume_liters": True,
            "oral_bioavailability": True,
            "absorption_rate_per_hour": True,
            "elimination_rate_per_hour": True,
            "derived_clearance_liters_per_hour": True,
        },
        "reference_absorption_duration_hours": True,
        "reference_clearance_liters_per_hour": True,
        "free_peak_10_mg_nmol_l": True,
        "iv_push_supported_amount_mg": True,
        "iv_push_supported_amounts_mg": True,
        "iv_push_scaling": True,
        "iv_push_initial_total_cortisol_nmol_l": True,
        "iv_push_elimination_rate_per_hour": True,
        "iv_push_elimination_half_life_hours": True,
        "contribution_horizon_hours": True,
        "sample_interval_minutes": True,
        "references": True,
    },
    "dose_markers": {
        "dose_event_id": True,
        "occurred_at": True,
        "local_time": True,
        "timezone": True,
        "utc_offset_minutes": True,
        "medication_name": True,
        "formulation": True,
        "amount": True,
        "unit": True,
        "route": True,
        "category": True,
        "source_type": True,
        "confirmation_state": True,
        "supported": True,
        "exclusion_reason": True,
        "carryover": True,
        "modeled_peak_at": True,
    },
    "samples": {
        "occurred_at": True,
        "local_time": True,
        "utc_offset_minutes": True,
        "modeled_free_cortisol_nmol_l": True,
        "regular_modeled_free_cortisol_nmol_l": True,
        "stress_modeled_free_cortisol_nmol_l": True,
        "derived_total_cortisol_nmol_l_display": True,
    },
    "supported_dose_count": True,
    "excluded_dose_count": True,
    "context_band": {
        "date": True,
        "timezone": True,
        "day_start": True,
        "day_end": True,
        "elapsed_hours": True,
        "series_kind": True,
        "series_name": True,
        "series_unit": True,
        "default_visible": True,
        "safety_label": True,
        "band": {
            "id": True,
            "revision": True,
            "interpolation": True,
            "lower_multiplier": True,
            "upper_multiplier": True,
            "anchor_origin": True,
            "healthy_rhythm_evidence_scope": True,
            "personalized": True,
            "body_context_used": True,
            "demographic_reference_interval": True,
            "references": True,
            "anchors": {"local_hour": True, "center_nmol_l": True},
        },
        "recorded_stress_context": {
            "episode_count": True,
            "missing_severity_count": True,
            "multiplier": True,
            "applied_to_band": True,
            "applied_to_drug_model": True,
            "reason": True,
        },
        "samples": {
            "occurred_at": True,
            "local_time": True,
            "utc_offset_minutes": True,
            "center_nmol_l": True,
            "lower_nmol_l": True,
            "upper_nmol_l": True,
        },
    },
    "wake_reference": {
        "available": True,
        "date": True,
        "timezone": True,
        "day_start": True,
        "day_end": True,
        "elapsed_hours": True,
        "series_kind": True,
        "series_unit": True,
        "reference": {
            "id": True,
            "revision": True,
            "binding_revision": True,
            "source_module": True,
            "sample_interval_minutes": True,
            "percentiles": True,
            "default_band": True,
            "references": {"citation": True, "pmid": True, "url": True},
        },
        "assumptions": {
            "healthy_adult_population_context_only": True,
            "wake_at": True,
            "sleep_onset_at": True,
            "age_years": True,
            "sex": True,
            "wake_amplitude_association_applied": True,
            "observed_meals": True,
            "unobserved_meals_invented": True,
            "pre_wake_gap_expected": True,
        },
        "missing_inputs": True,
        "safety_label": True,
        "samples": {
            "occurred_at": True,
            "local_time": True,
            "utc_offset_minutes": True,
            "hour_local": True,
            "hours_since_wake": True,
            "sigma_log": True,
            "serum_free_p5_nmol_l": True,
            "serum_free_p25_nmol_l": True,
            "serum_free_p50_nmol_l": True,
            "serum_free_p75_nmol_l": True,
            "serum_free_p95_nmol_l": True,
            "serum_total_p5_nmol_l": True,
            "serum_total_p25_nmol_l": True,
            "serum_total_p50_nmol_l": True,
            "serum_total_p75_nmol_l": True,
            "serum_total_p95_nmol_l": True,
        },
    },
    "coverage_features": {
        "available": True,
        "feature_id": True,
        "feature_revision": True,
        "date": True,
        "timezone": True,
        "analyzed_from": True,
        "analyzed_through": True,
        "elapsed_hours": True,
        "day_state": True,
        "safety_label": True,
        "definitions": True,
        "missing_inputs": True,
        "uncategorized_symptom_count": True,
        "comparison_minutes": True,
        "expected_pre_wake_excluded_minutes": True,
        "time_below_p5_minutes": True,
        "time_below_p25_minutes": True,
        "auc": {
            "modeled_free_nmol_l_hours": True,
            "regular_modeled_free_nmol_l_hours": True,
            "stress_modeled_free_nmol_l_hours": True,
            "reference_p50_nmol_l_hours": True,
            "modeled_minus_reference_p50_nmol_l_hours": True,
            "modeled_to_reference_p50_ratio": True,
        },
        "inter_dose_troughs": {
            "previous_dose_event_id": True,
            "next_dose_event_id": True,
            "occurred_at": True,
            "modeled_free_cortisol_nmol_l": True,
            "regular_modeled_free_cortisol_nmol_l": True,
            "stress_modeled_free_cortisol_nmol_l": True,
            "reference_p5_nmol_l": True,
            "reference_p25_nmol_l": True,
            "reference_p50_nmol_l": True,
            "depth_below_p50_nmol_l": True,
        },
        "maximum_fall": {
            "magnitude_nmol_l_per_hour": True,
            "interval_started_at": True,
            "interval_ended_at": True,
            "from_modeled_free_cortisol_nmol_l": True,
            "to_modeled_free_cortisol_nmol_l": True,
        },
        "p95_overshoot": {
            "duration_minutes": True,
            "maximum_nmol_l": True,
            "maximum_at": True,
        },
        "symptom_contexts": {
            "symptom_event_id": True,
            "occurred_at": True,
            "name": True,
            "severity": True,
            "tracking_category": True,
            "tracking_category_revision": True,
            "previous_supported_dose_event_ids": True,
            "previous_dose_categories": True,
            "minutes_since_previous_supported_dose": True,
            "modeled_free_cortisol_nmol_l": True,
            "regular_modeled_free_cortisol_nmol_l": True,
            "stress_modeled_free_cortisol_nmol_l": True,
            "reference_p5_nmol_l": True,
            "reference_p50_nmol_l": True,
            "reference_p95_nmol_l": True,
        },
    },
}

PRIVATE_IDENTIFIER_FIELDS: Final = frozenset(
    {
        "dose_event_id",
        "previous_dose_event_id",
        "next_dose_event_id",
        "symptom_event_id",
    }
)
PRIVATE_IDENTIFIER_LIST_FIELDS: Final = frozenset({"previous_supported_dose_event_ids"})
FORBIDDEN_PUBLIC_KEYS: Final = frozenset(
    {
        "owner_id",
        "email",
        "display_name",
        "notes",
        "correction_reason",
        "supersedes_id",
        "provider_id",
        "source_id",
        "source_revision_sha256",
        "revision_id",
        "recorded_at",
    }
)
PRIVATE_UUID_PATTERN: Final = re.compile(
    r"\b[0-9a-f]{8}-[0-9a-f]{4}-[1-5][0-9a-f]{3}-[89ab][0-9a-f]{3}-[0-9a-f]{12}\b",
    re.IGNORECASE,
)


@dataclass(slots=True)
class PublicIds:
    """Per-day rendering labels that cannot be correlated to private identifiers."""

    values: dict[str, str] = field(default_factory=dict)

    def map(self, value: object, kind: str = "record") -> str:
        private = str(value)
        existing = self.values.get(private)
        if existing is not None:
            return existing
        public = f"{kind}-{len(self.values) + 1}"
        self.values[private] = public
        return public


def eligibility_cutoff(day: date, timezone: str) -> datetime:
    """Return local noon on D+1, represented as an aware instant."""
    return datetime.combine(day + timedelta(days=1), time(hour=12), ZoneInfo(timezone))


def sync_qualifies(
    run: GarminSyncRun,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
    now: datetime,
) -> bool:
    """Apply ADR-0029's complete-day gate to one sync provenance row."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    cutoff = eligibility_cutoff(day, timezone)
    return (
        run.owner_id == owner_id
        and run.status in SUCCESSFUL_SYNC_STATUSES
        and run.timezone == timezone
        and run.requested_start_date <= day <= run.requested_end_date
        and run.finished_at >= cutoff
        and now >= cutoff
    )


def eligible_dates(
    session: Session,
    *,
    owner_id: uuid.UUID,
    timezone: str,
    now: datetime | None = None,
) -> list[date]:
    """Return every date covered by at least one qualifying successful sync."""
    instant = now or datetime.now(UTC)
    runs = session.scalars(
        select(GarminSyncRun).where(
            GarminSyncRun.owner_id == owner_id,
            GarminSyncRun.timezone == timezone,
            GarminSyncRun.status.in_(SUCCESSFUL_SYNC_STATUSES),
            GarminSyncRun.finished_at <= instant,
        )
    )
    dates: set[date] = set()
    for run in runs:
        current = run.requested_start_date
        while current <= run.requested_end_date:
            if sync_qualifies(
                run,
                owner_id=owner_id,
                day=current,
                timezone=timezone,
                now=instant,
            ):
                dates.add(current)
            current += timedelta(days=1)
    return sorted(dates)


def project_public(
    value: Any, projection: Projection | Literal[True], ids: PublicIds, field_name: str = ""
) -> Any:
    if projection is True:
        if field_name in PRIVATE_IDENTIFIER_FIELDS:
            return ids.map(value, field_name.removesuffix("_event_id"))
        if field_name in PRIVATE_IDENTIFIER_LIST_FIELDS:
            return [ids.map(item, "dose") for item in cast(list[object], value)]
        return value
    if value is None:
        return None
    if isinstance(value, list):
        return [project_public(item, projection, ids, field_name) for item in value]
    if not isinstance(value, Mapping):
        raise ValueError(f"public projection expected an object at {field_name or '<root>'}")
    return {
        key: project_public(value[key], child, ids, key)
        for key, child in projection.items()
        if key in value
    }


def _json(model: BaseModel) -> dict[str, Any]:
    return model.model_dump(mode="json")


def _time(value: BaseModel) -> dict[str, Any]:
    raw = _json(value)
    return {
        "occurred_at": raw["occurred_at"],
        "local_time": raw["local_time"],
        "timezone": raw["timezone"],
        "utc_offset_minutes": raw["utc_offset_minutes"],
    }


def _provenance(value: BaseModel) -> dict[str, Any]:
    raw = _json(value)
    return {
        "source_type": raw["source_type"],
        "confirmation_state": raw["confirmation_state"],
    }


def _garmin_record(record: Any, ids: PublicIds) -> dict[str, Any]:
    return {
        "id": ids.map(record.id, "garmin"),
        "kind": record.kind,
        "summary": record.summary,
        "time": _time(record.time),
        "provenance": _provenance(record.provenance),
        "metric_type": record.metric_type,
        "value": record.value,
        "unit": record.unit,
        "aggregation": record.aggregation,
        "sample_interval_seconds": record.sample_interval_seconds,
        "garmin_field_name": record.garmin_field_name,
        "measurement_label": record.measurement_label,
        "period_label": record.period_label,
        "ended_at": None if record.ended_at is None else record.ended_at.isoformat(),
        "duration_seconds": record.duration_seconds,
        "duration_source": record.duration_source,
        "awakenings": record.awakenings,
        "sleep_score": record.sleep_score,
        "sleep_intervals": [
            {
                "stage": interval.stage,
                "started_at": interval.started_at.isoformat(),
                "ended_at": interval.ended_at.isoformat(),
            }
            for interval in record.sleep_intervals
        ],
        "activity_type": record.activity_type,
        "distance_miles": record.distance_miles,
    }


def supported_activity_type(value: str | None) -> bool:
    """Limit public activity context to the owner's reviewed workout families."""
    if value is None:
        return False
    normalized = re.sub(r"[^a-z0-9]+", "_", value.strip().lower()).strip("_")
    return normalized in SUPPORTED_ACTIVITY_TYPES


def public_activity_records(records: Iterable[Any], *, day: date) -> list[Any]:
    """Select forward-only activity facts approved for the public curve."""
    if day < PUBLIC_ACTIVITY_START_DATE:
        return []
    return [
        record
        for record in records
        if record.kind == "activity" and supported_activity_type(record.activity_type)
    ]


def _symptom(record: Any, ids: PublicIds) -> dict[str, Any]:
    return {
        "id": ids.map(record.id, "symptom"),
        "category": "fact",
        "name": record.name,
        "severity": record.severity,
        "body_area": record.body_area,
        "tracking_category": record.tracking_category,
        "tracking_category_revision": record.tracking_category_revision,
        "time": _time(record.time),
        "provenance": _provenance(record.provenance),
        "episode_id": None if record.episode_id is None else ids.map(record.episode_id, "episode"),
    }


def _blood_pressure(record: Any, ids: PublicIds) -> dict[str, Any]:
    return {
        "id": ids.map(record.id, "blood-pressure"),
        "category": "fact",
        "systolic_mmhg": record.systolic_mmhg,
        "diastolic_mmhg": record.diastolic_mmhg,
        "pulse_bpm": record.pulse_bpm,
        "measurement_setting": record.measurement_setting,
        "body_position": record.body_position,
        "time": _time(record.time),
        "provenance": _provenance(record.provenance),
    }


def _temperature(record: Any, ids: PublicIds) -> dict[str, Any]:
    return {
        "id": ids.map(record.id, "temperature"),
        "category": "fact",
        "value": str(record.value),
        "unit": record.unit,
        "normalized_c": str(record.normalized_c),
        "display_f": str(record.display_f),
        "display_c": str(record.display_c),
        "time": _time(record.time),
        "provenance": _provenance(record.provenance),
    }


def _episode(record: Any, ids: PublicIds) -> dict[str, Any]:
    return {
        "id": ids.map(record.id, "episode"),
        "category": "fact",
        "trigger": record.trigger,
        "status": record.status,
        "severity": record.severity,
        "started_at": record.started_at.isoformat(),
        "ended_at": None if record.ended_at is None else record.ended_at.isoformat(),
        "timezone": record.timezone,
        "dose_count": record.dose_count,
        "symptom_count": record.symptom_count,
    }


def _all_pages(fetch: Callable[[PageRequest], Any], items: str = "items") -> list[Any]:
    first = fetch(PageRequest(page=1, page_size=100))
    results = list(getattr(first, items))
    for page_number in range(2, first.page.total_pages + 1):
        results.extend(getattr(fetch(PageRequest(page=page_number, page_size=100)), items))
    return results


def _day_records(session: Session, owner: Owner, day: date) -> tuple[list[Any], ...]:
    zone = owner.default_timezone
    daily = _all_pages(
        lambda page: garmin.records(
            session,
            owner,
            page,
            local_date_from=day,
            local_date_to=day,
            timezone=zone,
        ),
        "records",
    )
    samples = _all_pages(
        lambda page: garmin.samples(session, owner, page, day=day, timezone=zone),
        "records",
    )
    sleep = _all_pages(
        lambda page: garmin.list_sleep_records(session, owner, page, day=day, timezone=zone),
        "records",
    )
    symptoms = _all_pages(
        lambda page: events.list_symptoms(
            session,
            owner,
            page,
            local_date_from=day,
            local_date_to=day,
            timezone=zone,
        )
    )
    pressure = _all_pages(
        lambda page: vitals.list_blood_pressure(
            session,
            owner,
            page,
            local_date_from=day,
            local_date_to=day,
            timezone=zone,
        )
    )
    temperature = _all_pages(
        lambda page: vitals.list_temperature(
            session,
            owner,
            page,
            local_date_from=day,
            local_date_to=day,
            timezone=zone,
        )
    )
    episode_rows = _all_pages(
        lambda page: episodes.list_episodes(
            session,
            owner,
            page,
            local_date_from=day,
            local_date_to=day,
            timezone=zone,
            overlaps_window=True,
        )
    )
    activities = public_activity_records(daily, day=day)
    return (
        [record for record in daily if record.kind == "daily"] + activities + samples + sleep,
        symptoms,
        pressure,
        temperature,
        episode_rows,
    )


def _adjacent_vitals(session: Session, owner: Owner, day: date) -> tuple[list[Any], list[Any]]:
    pressure: list[Any] = []
    temperature: list[Any] = []
    for adjacent in (day - timedelta(days=1), day + timedelta(days=1)):
        pressure.extend(
            _all_pages(
                lambda page, adjacent=adjacent: vitals.list_blood_pressure(
                    session,
                    owner,
                    page,
                    local_date_from=adjacent,
                    local_date_to=adjacent,
                    timezone=owner.default_timezone,
                )
            )
        )
        temperature.extend(
            _all_pages(
                lambda page, adjacent=adjacent: vitals.list_temperature(
                    session,
                    owner,
                    page,
                    local_date_from=adjacent,
                    local_date_to=adjacent,
                    timezone=owner.default_timezone,
                )
            )
        )
    return pressure, temperature


def build_public_day(session: Session, *, owner: Owner, day: date) -> dict[str, Any]:
    """Build and privacy-validate one selected-day display payload."""
    raw_curve = analytics.steroid_exposure_curve(
        session,
        owner,
        day,
        owner.default_timezone,
        PUBLIC_MODEL,
    )
    curve_model = WakeFreeCortisolCurveOut.model_validate(raw_curve)
    curve = curve_model.model_dump(mode="json")
    ids = PublicIds()
    garmin_rows, symptoms, pressure, temperature, episode_rows = _day_records(session, owner, day)
    adjacent_pressure, adjacent_temperature = _adjacent_vitals(session, owner, day)
    payload = {
        "schema_version": PUBLIC_SCHEMA_VERSION,
        "date": day.isoformat(),
        "timezone": owner.default_timezone,
        "curve": project_public(curve, PUBLIC_CURVE_PROJECTION, ids),
        "garmin": [_garmin_record(record, ids) for record in garmin_rows],
        "symptoms": [_symptom(record, ids) for record in symptoms],
        "blood_pressure": [_blood_pressure(record, ids) for record in pressure],
        "temperature": [_temperature(record, ids) for record in temperature],
        "event_context_blood_pressure": [
            _blood_pressure(record, ids) for record in [*pressure, *adjacent_pressure]
        ],
        "event_context_temperature": [
            _temperature(record, ids) for record in [*temperature, *adjacent_temperature]
        ],
        "episodes": [_episode(record, ids) for record in episode_rows],
    }
    validate_public_payload(payload)
    return payload


def _walk_keys(value: Any) -> Iterable[str]:
    if isinstance(value, Mapping):
        for key, child in value.items():
            yield str(key)
            yield from _walk_keys(child)
    elif isinstance(value, list):
        for child in value:
            yield from _walk_keys(child)


def validate_public_payload(payload: Mapping[str, Any]) -> None:
    """Reject private keys, UUIDs, and malformed top-level publication contracts."""
    if payload.get("schema_version") != PUBLIC_SCHEMA_VERSION:
        raise ValueError("public payload schema version is missing or unsupported")
    forbidden = FORBIDDEN_PUBLIC_KEYS.intersection(_walk_keys(payload))
    if forbidden:
        raise ValueError(f"public payload contains forbidden keys: {sorted(forbidden)}")
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    if PRIVATE_UUID_PATTERN.search(encoded) is not None:
        raise ValueError("public payload contains a UUID-shaped private identifier")


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    path.write_text(
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def export_public_data(
    session: Session,
    *,
    owner: Owner,
    output_directory: Path,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Generate a complete data tree and replace the prior local tree as one unit."""
    dates = eligible_dates(
        session,
        owner_id=owner.id,
        timezone=owner.default_timezone,
        now=now,
    )
    if not dates:
        raise ValueError("no dates satisfy the public publication gate")

    parent = output_directory.resolve().parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(mkdtemp(prefix=f".{output_directory.name}-", dir=parent))
    previous = parent / f".{output_directory.name}-previous"
    try:
        day_directory = staging / "days"
        day_directory.mkdir()
        for day in dates:
            _write_json(
                day_directory / f"{day.isoformat()}.json",
                build_public_day(session, owner=owner, day=day),
            )
        manifest = {
            "schema_version": PUBLIC_SCHEMA_VERSION,
            "timezone": owner.default_timezone,
            "newest_date": dates[-1].isoformat(),
            "dates": [day.isoformat() for day in dates],
        }
        _write_json(staging / "manifest.json", manifest)
        digest = hashlib.sha256()
        for path in sorted(staging.rglob("*.json")):
            digest.update(path.relative_to(staging).as_posix().encode())
            digest.update(path.read_bytes())
        _write_json(
            staging / "publication.json",
            {
                "schema_version": PUBLIC_SCHEMA_VERSION,
                "file_count": len(dates) + 2,
                "bundle_sha256": digest.hexdigest(),
            },
        )

        if previous.exists():
            shutil.rmtree(previous)
        if output_directory.exists():
            os.replace(output_directory, previous)
        os.replace(staging, output_directory)
        if previous.exists():
            shutil.rmtree(previous)
        return manifest
    except BaseException:
        if staging.exists():
            shutil.rmtree(staging)
        if not output_directory.exists() and previous.exists():
            os.replace(previous, output_directory)
        raise
