"""Durable scheduled jobs for the isolated Garmin Connect worker."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.config import Settings
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.connect_client import (
    GarminProviderError,
    GarminReadClient,
    PythonGarminReadClient,
)
from healthcurve.integrations.garmin.connect_sync import fetch_window, persist_window
from healthcurve.integrations.garmin.models import GarminConnection, GarminConnectionState
from healthcurve.operations.jobs import Job, JobQueueError, enqueue
from healthcurve.operations.worker import JobHandler

GARMIN_SYNC_TASK = "garmin.connect.sync"
GARMIN_DISCONNECT_TASK = "garmin.connect.disconnect"


def enqueue_sync(
    session: Session,
    *,
    owner_id: uuid.UUID,
    start_date: date,
    end_date: date,
    timezone: str,
    idempotency_key: str,
) -> Job:
    if end_date < start_date or (end_date - start_date).days >= 31:
        raise JobQueueError("garmin_sync_window_invalid")
    return enqueue(
        session,
        task=GARMIN_SYNC_TASK,
        payload={
            "owner_id": str(owner_id),
            "start_date": start_date.isoformat(),
            "end_date": end_date.isoformat(),
            "timezone": timezone,
        },
        idempotency_key=idempotency_key,
        priority=20,
        max_attempts=5,
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
        local_day = now.astimezone(ZoneInfo(owner.default_timezone)).date()
        first = local_day - timedelta(days=settings.garmin_sync_lookback_days - 1)
        if connection.checkpoint_date is not None:
            first = max(first, connection.checkpoint_date - timedelta(days=2))
        enqueue_sync(
            session,
            owner_id=owner.id,
            start_date=first,
            end_date=local_day,
            timezone=owner.default_timezone,
            idempotency_key=f"scheduled:{owner.id}:{local_day.isoformat()}",
        )


def make_garmin_handler(
    settings: Settings,
    *,
    client_factory: Callable[[], GarminReadClient] | None = None,
) -> JobHandler:
    factory = client_factory or (lambda: _configured_client(settings))

    def handle(session: Session, payload: Mapping[str, Any]) -> None:
        owner_id, start_date, end_date, timezone = _payload(payload)
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
        persist_window(session, owner_id=owner_id, fetched=fetched)
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


def _payload(payload: Mapping[str, Any]) -> tuple[uuid.UUID, date, date, str]:
    if set(payload) != {"owner_id", "start_date", "end_date", "timezone"}:
        raise JobQueueError("garmin_job_payload_invalid")
    try:
        owner_id = uuid.UUID(str(payload["owner_id"]))
        start_date = date.fromisoformat(str(payload["start_date"]))
        end_date = date.fromisoformat(str(payload["end_date"]))
        timezone = ZoneInfo(str(payload["timezone"])).key
    except (ValueError, TypeError, KeyError) as exc:
        raise JobQueueError("garmin_job_payload_invalid") from exc
    if end_date < start_date or (end_date - start_date).days >= 31:
        raise JobQueueError("garmin_sync_window_invalid")
    return owner_id, start_date, end_date, timezone
