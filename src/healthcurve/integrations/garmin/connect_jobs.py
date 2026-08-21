"""Durable scheduled jobs for the isolated Garmin Connect worker."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from enum import StrEnum
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.config import Settings
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.connect_client import (
    GarminIntradayReadClient,
    GarminProviderError,
    GarminReadClient,
    PythonGarminReadClient,
)
from healthcurve.integrations.garmin.connect_sync import fetch_window, persist_window
from healthcurve.integrations.garmin.models import (
    GarminConnection,
    GarminConnectionState,
    GarminSyncOrigin,
    GarminSyncRun,
    GarminSyncStatus,
)
from healthcurve.operations.jobs import Job, JobQueueError, JobStatus, enqueue
from healthcurve.operations.worker import JobHandler

GARMIN_SYNC_TASK = "garmin.connect.sync"
GARMIN_DISCONNECT_TASK = "garmin.connect.disconnect"
GARMIN_COMPLETED_WINDOW_COOLDOWN = timedelta(minutes=30)


class GarminSyncDisposition(StrEnum):
    QUEUED = "queued"
    REFRESH_QUEUED = "refresh_queued"
    COALESCED_ACTIVE = "coalesced_active"
    COOLDOWN_REUSED = "cooldown_reused"
    IDEMPOTENT_REPLAY = "idempotent_replay"


@dataclass(frozen=True)
class GarminSyncEnqueueResult:
    job: Job
    disposition: GarminSyncDisposition
    cooldown_until: datetime | None = None


def enqueue_sync(
    session: Session,
    *,
    owner_id: uuid.UUID,
    start_date: date,
    end_date: date,
    timezone: str,
    idempotency_key: str,
    force_refresh: bool = False,
    origin: GarminSyncOrigin = GarminSyncOrigin.MANUAL,
    now: datetime | None = None,
) -> GarminSyncEnqueueResult:
    if end_date < start_date or (end_date - start_date).days >= 31:
        raise JobQueueError("garmin_sync_window_invalid")
    requested_at = _utc_now(now)
    try:
        timezone = ZoneInfo(timezone).key
    except (ValueError, TypeError, KeyError) as exc:
        raise JobQueueError("garmin_job_payload_invalid") from exc
    if force_refresh:
        if origin not in {GarminSyncOrigin.MANUAL, GarminSyncOrigin.MANUAL_REFRESH}:
            raise JobQueueError("garmin_sync_origin_invalid")
        origin = GarminSyncOrigin.MANUAL_REFRESH
    payload = {
        "owner_id": str(owner_id),
        "start_date": start_date.isoformat(),
        "end_date": end_date.isoformat(),
        "timezone": timezone,
        "origin": origin.value,
    }

    # A running handler holds the owner connection lock while contacting Garmin. The
    # unlocked fast path lets a duplicate web request immediately reference that job
    # instead of waiting for the provider call. A second check under the connection
    # lock closes the enqueue race when no equivalent job is yet committed.
    existing = _existing_sync_request(session, payload=payload, idempotency_key=idempotency_key)
    if existing is not None:
        return existing

    connection = session.scalar(
        select(GarminConnection).where(GarminConnection.owner_id == owner_id).with_for_update()
    )
    if connection is None or connection.state is not GarminConnectionState.CONNECTED:
        raise JobQueueError("garmin_connection_not_enabled")
    existing = _existing_sync_request(session, payload=payload, idempotency_key=idempotency_key)
    if existing is not None:
        return existing

    latest = session.scalar(
        select(Job)
        .where(
            Job.task == GARMIN_SYNC_TASK,
            *_window_clauses(payload),
            Job.status == JobStatus.COMPLETED,
            Job.finished_at.is_not(None),
        )
        .order_by(Job.finished_at.desc(), Job.id.desc())
        .limit(1)
    )
    cooldown_until = _cooldown_until(latest)
    if (
        not force_refresh
        and latest is not None
        and cooldown_until is not None
        and requested_at < cooldown_until
    ):
        return GarminSyncEnqueueResult(
            job=latest,
            disposition=GarminSyncDisposition.COOLDOWN_REUSED,
            cooldown_until=cooldown_until,
        )

    job = enqueue(
        session,
        task=GARMIN_SYNC_TASK,
        payload=payload,
        idempotency_key=idempotency_key,
        run_at=requested_at,
        priority=20,
        max_attempts=5,
    )
    return GarminSyncEnqueueResult(
        job=job,
        disposition=(
            GarminSyncDisposition.REFRESH_QUEUED
            if force_refresh and latest is not None
            else GarminSyncDisposition.QUEUED
        ),
    )


def enqueue_disconnect(session: Session, *, owner_id: uuid.UUID, idempotency_key: str) -> Job:
    return enqueue(
        session,
        task=GARMIN_DISCONNECT_TASK,
        payload={"owner_id": str(owner_id)},
        idempotency_key=idempotency_key,
        priority=100,
        max_attempts=5,
    )


def schedule_garmin_sync(session: Session, now: datetime, *, settings: Settings) -> None:
    if not settings.garmin_enabled:
        return
    rows = session.execute(
        select(GarminConnection, Owner)
        .join(Owner, Owner.id == GarminConnection.owner_id)
        .where(GarminConnection.state == GarminConnectionState.CONNECTED)
    )
    for connection, owner in rows:
        owner_zone = ZoneInfo(owner.default_timezone)
        local_now = now.astimezone(owner_zone)
        if local_now.time() < time(hour=settings.garmin_sync_hour_local):
            continue
        local_day = local_now.date()
        first = local_day - timedelta(days=connection.sync_lookback_days - 1)
        daily_key = f"scheduled:{owner.id}:{local_day.isoformat()}"
        already_scheduled = session.scalar(
            select(Job.id).where(
                Job.task == GARMIN_SYNC_TASK,
                Job.idempotency_key == daily_key,
            )
        )
        if already_scheduled is not None:
            continue

        # A manual request can be the one durable job for today's exact scheduler
        # window. Once that job succeeds, its checkpoint narrows the next poll's
        # reconciliation window. The completed provider read still covers that
        # narrower window, so do not create a second same-day read merely because the
        # calculated start date changed. A run that does not cover the full required
        # window (or that ended on an earlier local day) does not suppress scheduling.
        local_midnight = datetime.combine(local_day, time.min, tzinfo=owner_zone).astimezone(UTC)
        covering_run = session.scalar(
            select(GarminSyncRun.id)
            .where(
                GarminSyncRun.owner_id == owner.id,
                GarminSyncRun.timezone == owner.default_timezone,
                GarminSyncRun.requested_start_date <= first,
                GarminSyncRun.requested_end_date >= local_day,
                GarminSyncRun.status.in_(
                    (GarminSyncStatus.COMPLETED, GarminSyncStatus.COMPLETED_WITH_WARNINGS)
                ),
                GarminSyncRun.finished_at >= local_midnight,
            )
            .limit(1)
        )
        if covering_run is not None:
            continue
        enqueue_sync(
            session,
            owner_id=owner.id,
            start_date=first,
            end_date=local_day,
            timezone=owner.default_timezone,
            idempotency_key=daily_key,
            origin=GarminSyncOrigin.SCHEDULED,
            now=now,
        )


def make_garmin_handler(
    settings: Settings,
    *,
    client_factory: Callable[[], GarminIntradayReadClient] | None = None,
) -> JobHandler:
    factory = client_factory or (lambda: _configured_client(settings))

    def handle(session: Session, payload: Mapping[str, Any]) -> None:
        owner_id, start_date, end_date, timezone, origin = _payload(payload)
        connection = session.scalar(
            select(GarminConnection).where(GarminConnection.owner_id == owner_id).with_for_update()
        )
        if connection is None or connection.state is not GarminConnectionState.CONNECTED:
            # A disconnect can race with an already queued sync. The owner-approved
            # disconnect wins: stale work becomes a clean no-op instead of retrying
            # into a dead letter or contacting Garmin after consent was withdrawn.
            return
        try:
            fetched = fetch_window(
                factory(), start_date=start_date, end_date=end_date, timezone=timezone
            )
        except GarminProviderError as exc:
            if exc.retryable:
                raise JobQueueError(exc.reason_code) from None
            connection.last_error_code = exc.reason_code
            if exc.reason_code in {
                "garmin_authentication_required",
                "garmin_mfa_required",
                "garmin_token_store_missing",
            }:
                connection.state = GarminConnectionState.REAUTHENTICATION_REQUIRED
            return
        except (ValueError, OverflowError, TypeError):
            connection.last_error_code = "garmin_response_invalid"
            return
        persist_window(session, owner_id=owner_id, fetched=fetched, origin=origin)
        connection.last_error_code = None

    return handle


def make_disconnect_handler(
    settings: Settings,
    *,
    client_factory: Callable[[], GarminReadClient] | None = None,
) -> JobHandler:
    factory = client_factory or (lambda: _configured_client(settings))

    def handle(session: Session, payload: Mapping[str, Any]) -> None:
        if set(payload) != {"owner_id"}:
            raise JobQueueError("garmin_job_payload_invalid")
        try:
            owner_id = uuid.UUID(str(payload["owner_id"]))
        except (ValueError, TypeError) as exc:
            raise JobQueueError("garmin_job_payload_invalid") from exc
        connection = session.scalar(
            select(GarminConnection).where(GarminConnection.owner_id == owner_id).with_for_update()
        )
        if connection is None:
            return
        if connection.state is GarminConnectionState.DISCONNECTED:
            return
        if connection.state is not GarminConnectionState.DISCONNECT_PENDING:
            raise JobQueueError("garmin_disconnect_not_confirmed")
        try:
            factory().logout()
        except GarminProviderError as exc:
            raise JobQueueError(exc.reason_code) from None
        connection.state = GarminConnectionState.DISCONNECTED
        connection.disconnected_at = datetime.now(UTC)
        connection.last_error_code = None

    return handle


def _configured_client(settings: Settings) -> PythonGarminReadClient:
    if not settings.garmin_enabled or settings.garmin_token_store is None:
        raise GarminProviderError("garmin_not_configured", retryable=False)
    return PythonGarminReadClient(
        email=None,
        password=None,
        token_store=settings.garmin_token_store,
    )


def _utc_now(value: datetime | None) -> datetime:
    current = value or datetime.now(UTC)
    if current.tzinfo is None or current.utcoffset() is None:
        raise JobQueueError("garmin_sync_time_invalid")
    return current.astimezone(UTC)


def _cooldown_until(job: Job | None) -> datetime | None:
    if job is None or job.status is not JobStatus.COMPLETED or job.finished_at is None:
        return None
    return job.finished_at + GARMIN_COMPLETED_WINDOW_COOLDOWN


def _existing_sync_request(
    session: Session, *, payload: Mapping[str, Any], idempotency_key: str
) -> GarminSyncEnqueueResult | None:
    exact = session.scalar(
        select(Job).where(Job.task == GARMIN_SYNC_TASK, Job.idempotency_key == idempotency_key)
    )
    if exact is not None:
        if not _idempotency_payloads_compatible(exact.payload, payload):
            raise JobQueueError("garmin_idempotency_conflict")
        disposition = (
            GarminSyncDisposition.COALESCED_ACTIVE
            if exact.status in {JobStatus.QUEUED, JobStatus.RUNNING}
            else GarminSyncDisposition.IDEMPOTENT_REPLAY
        )
        return GarminSyncEnqueueResult(
            job=exact,
            disposition=disposition,
            cooldown_until=_cooldown_until(exact),
        )

    active = session.scalar(
        select(Job)
        .where(
            Job.task == GARMIN_SYNC_TASK,
            *_window_clauses(payload),
            Job.status.in_((JobStatus.QUEUED, JobStatus.RUNNING)),
        )
        .order_by(Job.started_at.desc().nullslast(), Job.created_at, Job.id)
        .limit(1)
    )
    if active is None:
        return None
    return GarminSyncEnqueueResult(
        job=active,
        disposition=GarminSyncDisposition.COALESCED_ACTIVE,
    )


def _payload(
    payload: Mapping[str, Any],
) -> tuple[uuid.UUID, date, date, str, GarminSyncOrigin]:
    legacy_keys = {"owner_id", "start_date", "end_date", "timezone"}
    if frozenset(payload) not in {frozenset(legacy_keys), frozenset({*legacy_keys, "origin"})}:
        raise JobQueueError("garmin_job_payload_invalid")
    try:
        owner_id = uuid.UUID(str(payload["owner_id"]))
        start_date = date.fromisoformat(str(payload["start_date"]))
        end_date = date.fromisoformat(str(payload["end_date"]))
        timezone = ZoneInfo(str(payload["timezone"])).key
        origin = GarminSyncOrigin(str(payload.get("origin", GarminSyncOrigin.LEGACY.value)))
    except (ValueError, TypeError, KeyError) as exc:
        raise JobQueueError("garmin_job_payload_invalid") from exc
    if end_date < start_date or (end_date - start_date).days >= 31:
        raise JobQueueError("garmin_sync_window_invalid")
    return owner_id, start_date, end_date, timezone, origin


def _window_clauses(payload: Mapping[str, Any]) -> tuple[Any, ...]:
    return tuple(
        Job.payload[key].as_string() == str(payload[key])
        for key in ("owner_id", "start_date", "end_date", "timezone")
    )


def _idempotency_payloads_compatible(
    stored: Mapping[str, Any], requested: Mapping[str, Any]
) -> bool:
    window_keys = ("owner_id", "start_date", "end_date", "timezone")
    if any(stored.get(key) != requested.get(key) for key in window_keys):
        return False
    stored_origin = stored.get("origin", GarminSyncOrigin.LEGACY.value)
    requested_origin = requested.get("origin", GarminSyncOrigin.LEGACY.value)
    return (
        stored_origin == requested_origin
        or stored_origin == GarminSyncOrigin.LEGACY.value
        or requested_origin == GarminSyncOrigin.LEGACY.value
    )
