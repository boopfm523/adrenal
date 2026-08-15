"""Bounded Garmin Connect fetch and idempotent fact reconciliation."""

from __future__ import annotations

import time as time_module
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from importlib.metadata import version
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, EventMixin, SourceType
from healthcurve.events.timekeeping import EventTime, resolve_event_time
from healthcurve.integrations.garmin.connect_client import GarminIntradayReadClient
from healthcurve.integrations.garmin.connect_intraday import (
    IntradayObservation,
    map_intraday_day,
)
from healthcurve.integrations.garmin.connect_mapping import (
    ActivityObservation,
    DailyObservation,
    SleepObservation,
    map_activities,
    map_day,
)
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminConnection,
    GarminConnectionState,
    GarminMetricEvent,
    GarminSleepEvent,
    GarminSleepStage,
    GarminSleepStageInterval,
    GarminSyncOrigin,
    GarminSyncRun,
    GarminSyncStatus,
)
from healthcurve.operations import audit

MAX_SYNC_DAYS: Final = 31
GARMINCONNECT_VERSION: Final = version("garminconnect")


class GarminSyncError(RuntimeError):
    def __init__(self, reason_code: str, *, retryable: bool = False) -> None:
        self.reason_code = reason_code
        self.retryable = retryable
        super().__init__(reason_code)


@dataclass(frozen=True)
class FetchedWindow:
    start_date: date
    end_date: date
    timezone: str
    metrics: tuple[DailyObservation, ...]
    intraday_metrics: tuple[IntradayObservation, ...]
    sleeps: tuple[SleepObservation, ...]
    activities: tuple[ActivityObservation, ...]
    warnings: tuple[str, ...]
    capabilities: dict[str, str]
    started_at: datetime
    finished_at: datetime


@dataclass(frozen=True)
class SyncResult:
    run: GarminSyncRun
    created: int
    corrected: int
    unchanged: int


def fetch_window(
    client: GarminIntradayReadClient,
    *,
    start_date: date,
    end_date: date,
    timezone: str,
    minimum_call_interval_s: float = 0.25,
    monotonic: Callable[[], float] = time_module.monotonic,
    pause: Callable[[float], None] = time_module.sleep,
) -> FetchedWindow:
    _validate_window(start_date, end_date, timezone)
    started_at = datetime.now(UTC)
    metrics: list[DailyObservation] = []
    intraday_metrics: list[IntradayObservation] = []
    sleeps: list[SleepObservation] = []
    warnings: list[str] = []
    capabilities: dict[str, str] = {}
    last_call: float | None = None

    def call[T](operation: Callable[[], T]) -> T:
        nonlocal last_call
        now = monotonic()
        if last_call is not None:
            remaining = minimum_call_interval_s - (now - last_call)
            if remaining > 0:
                pause(remaining)
        result = operation()
        last_call = monotonic()
        return result

    client.login()
    current = start_date
    while current <= end_date:
        day_text = current.isoformat()
        mapped = map_day(
            day=current,
            stats=call(lambda day_text=day_text: client.get_stats(day_text)),
            sleep=call(lambda day_text=day_text: client.get_sleep_data(day_text)),
            timezone=timezone,
        )
        metrics.extend(mapped.metrics)
        if mapped.sleep is not None:
            sleeps.append(mapped.sleep)
        warnings.extend(mapped.warnings)
        for name, state in mapped.capabilities.items():
            already_available = capabilities.get(name) == "available"
            capabilities[name] = "available" if state == "available" or already_available else state
        intraday = map_intraday_day(
            day=current,
            heart_rate=call(lambda day_text=day_text: client.get_heart_rates(day_text)),
            stress=call(lambda day_text=day_text: client.get_stress_data(day_text)),
            respiration=call(lambda day_text=day_text: client.get_respiration_data(day_text)),
            hrv=call(lambda day_text=day_text: client.get_hrv_data(day_text)),
            steps=call(lambda day_text=day_text: client.get_steps_data(day_text)),
            timezone=timezone,
        )
        intraday_metrics.extend(intraday.observations)
        metrics.extend(intraday.aggregates)
        warnings.extend(intraday.warnings)
        for name, state in intraday.capabilities.items():
            already_available = capabilities.get(name) == "available"
            capabilities[name] = "available" if state == "available" or already_available else state
        current += timedelta(days=1)

    raw_activities = call(
        lambda: client.get_activities_by_date(start_date.isoformat(), end_date.isoformat())
    )
    activities, activity_warnings = map_activities(raw_activities, timezone=timezone)
    warnings.extend(activity_warnings)
    capabilities["activities"] = "available" if activities else "unavailable"
    return FetchedWindow(
        start_date=start_date,
        end_date=end_date,
        timezone=timezone,
        metrics=tuple(metrics),
        intraday_metrics=tuple(intraday_metrics),
        sleeps=tuple(sleeps),
        activities=activities,
        warnings=tuple(sorted(set(warnings)))[:100],
        capabilities=capabilities,
        started_at=started_at,
        finished_at=datetime.now(UTC),
    )


def persist_window(
    session: Session,
    *,
    owner_id: uuid.UUID,
    fetched: FetchedWindow,
    origin: GarminSyncOrigin = GarminSyncOrigin.LEGACY,
) -> SyncResult:
    connection = session.scalar(
        select(GarminConnection).where(GarminConnection.owner_id == owner_id).with_for_update()
    )
    if connection is None or connection.state is not GarminConnectionState.CONNECTED:
        raise GarminSyncError("garmin_connection_not_enabled")

    run = GarminSyncRun(
        id=uuid.uuid4(),
        owner_id=owner_id,
        requested_start_date=fetched.start_date,
        requested_end_date=fetched.end_date,
        timezone=fetched.timezone,
        origin=origin,
        status=(
            GarminSyncStatus.COMPLETED_WITH_WARNINGS
            if fetched.warnings
            else GarminSyncStatus.COMPLETED
        ),
        started_at=fetched.started_at,
        finished_at=fetched.finished_at,
        counts={},
        warning_codes=list(fetched.warnings),
        client_version=GARMINCONNECT_VERSION,
    )
    session.add(run)
    session.flush([run])

    created = corrected = unchanged = 0
    for observation in fetched.metrics:
        outcome = _upsert_metric(session, owner_id, run.id, fetched.timezone, observation)
        created += outcome == "created"
        corrected += outcome == "corrected"
        unchanged += outcome == "unchanged"
    for observation in fetched.intraday_metrics:
        outcome = _upsert_intraday_metric(session, owner_id, run.id, observation)
        created += outcome == "created"
        corrected += outcome == "corrected"
        unchanged += outcome == "unchanged"
    for observation in fetched.sleeps:
        outcome = _upsert_sleep(session, owner_id, run.id, observation)
        created += outcome == "created"
        corrected += outcome == "corrected"
        unchanged += outcome == "unchanged"
    for observation in fetched.activities:
        outcome = _upsert_activity(session, owner_id, run.id, observation)
        created += outcome == "created"
        corrected += outcome == "corrected"
        unchanged += outcome == "unchanged"

    run.counts = {
        "metrics": len(fetched.metrics),
        "intraday_metrics": len(fetched.intraday_metrics),
        "sleep": len(fetched.sleeps),
        "activities": len(fetched.activities),
        "created": created,
        "corrected": corrected,
        "unchanged": unchanged,
    }
    connection.last_success_at = fetched.finished_at
    connection.checkpoint_date = fetched.end_date
    connection.capabilities = fetched.capabilities
    connection.client_version = GARMINCONNECT_VERSION
    audit.record(
        session,
        actor="system",
        action=audit.AuditAction.INTEGRATION_SYNC_COMPLETED,
        target_type="garmin_sync_run",
        target_id=run.id,
        change_summary=(
            f"created={created};corrected={corrected};unchanged={unchanged};"
            f"warnings={len(fetched.warnings)}"
        ),
    )
    return SyncResult(run=run, created=created, corrected=corrected, unchanged=unchanged)


def _upsert_metric(
    session: Session,
    owner_id: uuid.UUID,
    run_id: uuid.UUID,
    timezone: str,
    value: DailyObservation,
) -> str:
    provider_id = _owned_provider_id(owner_id, value.provider_id)
    next_midnight = resolve_event_time(
        datetime.combine(value.day + timedelta(days=1), time.min), timezone
    ).occurred_at
    fields = {
        **_source_fields(run_id, provider_id, value.revision, "daily-summary"),
        "metric_type": value.metric_type,
        "value": value.value,
        "unit": value.unit,
        "period_end_at": next_midnight,
        "aggregation": "daily_summary",
        "sample_interval_seconds": None,
        "garmin_field_name": value.field_name,
    }
    return _upsert_event(
        session, GarminMetricEvent, owner_id, provider_id, value.revision, value.event_time, fields
    )[0]


def _upsert_intraday_metric(
    session: Session,
    owner_id: uuid.UUID,
    run_id: uuid.UUID,
    value: IntradayObservation,
) -> str:
    provider_id = _owned_provider_id(owner_id, value.provider_id)
    fields = {
        **_source_fields(run_id, provider_id, value.revision, "intraday-sample"),
        "metric_type": value.metric_type,
        "value": value.value,
        "unit": value.unit,
        "period_end_at": None,
        "aggregation": "provider_sample",
        "sample_interval_seconds": value.sample_interval_seconds,
        "garmin_field_name": value.field_name,
    }
    return _upsert_event(
        session, GarminMetricEvent, owner_id, provider_id, value.revision, value.event_time, fields
    )[0]


def _upsert_sleep(
    session: Session, owner_id: uuid.UUID, run_id: uuid.UUID, value: SleepObservation
) -> str:
    provider_id = _owned_provider_id(owner_id, value.provider_id)
    fields = {
        **_source_fields(run_id, provider_id, value.revision, "daily-sleep"),
        "ended_at": value.ended_at,
        "overall_sleep_score": value.score,
        "stage_count": value.stage_count,
        "duration_seconds": value.duration_seconds,
        "garmin_duration_source": value.duration_source,
        "awakenings": value.awakenings,
    }
    outcome, row = _upsert_event(
        session, GarminSleepEvent, owner_id, provider_id, value.revision, value.event_time, fields
    )
    if outcome != "unchanged":
        session.flush([row])
        session.add_all(
            GarminSleepStageInterval(
                sleep_event_id=row.id,
                ordinal=ordinal,
                stage=GarminSleepStage.AWAKE,
                started_at=interval.started_at,
                ended_at=interval.ended_at,
            )
            for ordinal, interval in enumerate(value.stage_intervals)
        )
    return outcome


def _upsert_activity(
    session: Session, owner_id: uuid.UUID, run_id: uuid.UUID, value: ActivityObservation
) -> str:
    provider_id = _owned_provider_id(owner_id, value.provider_id)
    fields = {
        **_source_fields(run_id, provider_id, value.revision, "activity-list"),
        "ended_at": value.ended_at,
        "sport": value.sport,
        "sub_sport": None,
        "title": value.title,
        "elapsed_seconds": value.elapsed_seconds,
        "distance_miles": value.distance_miles,
        "calories": None,
        "average_heart_rate": None,
        "maximum_heart_rate": None,
        "source_notes": None,
    }
    return _upsert_event(
        session,
        GarminActivityEvent,
        owner_id,
        provider_id,
        value.revision,
        value.event_time,
        fields,
    )[0]


def _upsert_event[E: EventMixin](
    session: Session,
    model: type[E],
    owner_id: uuid.UUID,
    provider_id: str,
    revision: str,
    event_time: EventTime,
    fields: dict[str, Any],
) -> tuple[str, E]:
    rows = list(
        session.scalars(
            select(model).where(
                model.owner_id == owner_id,
                model.source_type == SourceType.PROVIDER,
                model.provider_id == provider_id,
            )
        )
    )
    current = events.current_only(session, model, rows)
    head = current[0] if current else None
    if head is not None and head.source_revision == revision:
        return "unchanged", head
    if head is None:
        created = events.create_event(
            session,
            model,
            owner_id=owner_id,
            event_time=event_time,
            source_type=SourceType.PROVIDER,
            confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
            **fields,
        )
        return "created", created
    corrected = events.correct_event(
        session,
        model,
        head,
        reason="Garmin provider revision changed",
        changes=fields,
        event_time=event_time,
    )
    return "corrected", corrected


def _source_fields(
    run_id: uuid.UUID, provider_id: str, revision: str, source_member: str
) -> dict[str, object]:
    return {
        "provider_id": provider_id,
        "source_revision": revision,
        "import_batch_id": None,
        "garmin_import_batch_id": None,
        "garmin_sync_run_id": run_id,
        "garmin_source_member": source_member,
        "garmin_manufacturer": "Garmin",
        "garmin_product_name": None,
        "garmin_device_serial_hash": None,
        "notes": None,
    }


def _owned_provider_id(owner_id: uuid.UUID, provider_id: str) -> str:
    return f"garmin:{owner_id}:{provider_id}"


def _validate_window(start_date: date, end_date: date, timezone: str) -> None:
    if end_date < start_date or (end_date - start_date).days + 1 > MAX_SYNC_DAYS:
        raise GarminSyncError("garmin_sync_window_invalid")
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise GarminSyncError("garmin_timezone_invalid") from exc
