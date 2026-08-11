"""Owner-reviewed import of Garmin export files.

Preview is deliberately database-free. Confirmation reparses the exact upload and
checks its digest before any recorded fact is created (SAFE-11, SAFE-14).
"""

from __future__ import annotations

import hmac
import uuid
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Annotated, Any, Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from healthcurve.api.deps import AppSettings, CurrentOwner, DbSession, require_csrf
from healthcurve.api.routers.events import provenance_out, time_out
from healthcurve.api.schemas import EventTimeOut, ProvenanceOut
from healthcurve.events import service as events
from healthcurve.integrations.garmin.connect_jobs import GarminSyncDisposition, enqueue_sync
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminConnection,
    GarminConnectionState,
    GarminMetricEvent,
    GarminSleepEvent,
    GarminSyncRun,
)
from healthcurve.integrations.garmin.parser import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_METRICS,
    ActivityCandidate,
    GarminImportError,
    MetricCandidate,
    ParsedGarminImport,
    SleepCandidate,
    parse_upload,
)
from healthcurve.integrations.garmin.service import confirm_import
from healthcurve.operations.jobs import JobQueueError

router = APIRouter(prefix="/integrations/garmin", tags=["garmin"])


class GarminStatusOut(BaseModel):
    configured: bool
    state: str
    last_success_at: datetime | None = None
    checkpoint_date: date | None = None
    capabilities: dict[str, str] = Field(default_factory=dict)
    last_error_code: str | None = None
    client_version: str | None = None
    latest_sync_status: str | None = None
    latest_sync_warning_codes: list[str] = Field(default_factory=list)


class GarminRecordOut(BaseModel):
    id: uuid.UUID
    kind: Literal["daily", "sleep", "activity"]
    summary: str
    time: EventTimeOut
    provenance: ProvenanceOut
    metric_type: str | None = None
    value: str | None = None
    unit: str | None = None
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    duration_source: str | None = None
    awakenings: int | None = None
    sleep_score: int | None = None
    activity_type: str | None = None
    distance_miles: str | None = None


class GarminRecordsOut(BaseModel):
    records: list[GarminRecordOut]
    notice: str = (
        "Garmin observations are recorded facts, not diagnoses or medication guidance. "
        "Unavailable provider values remain missing rather than zero."
    )


class GarminDisconnectPreviewOut(BaseModel):
    state: str
    automatic_fact_rows: int
    reviewed_import_fact_rows: int
    sync_run_rows: int
    delete_data_confirmation: str = "DISCONNECT GARMIN AND DELETE DATA"
    retain_data_confirmation: str = "DISCONNECT GARMIN"


class GarminSyncRequestOut(BaseModel):
    job_id: uuid.UUID
    status: str
    disposition: GarminSyncDisposition
    requested_start_date: date
    requested_end_date: date
    cooldown_until: datetime | None = None


@router.get("/status", response_model=GarminStatusOut)
def connection_status(
    session: DbSession, owner: CurrentOwner, settings: AppSettings
) -> GarminStatusOut:
    connection = session.scalar(
        select(GarminConnection).where(GarminConnection.owner_id == owner.id)
    )
    latest = session.scalar(
        select(GarminSyncRun)
        .where(GarminSyncRun.owner_id == owner.id)
        .order_by(GarminSyncRun.finished_at.desc(), GarminSyncRun.id.desc())
        .limit(1)
    )
    return GarminStatusOut(
        configured=settings.garmin_enabled,
        state="not_connected" if connection is None else connection.state.value,
        last_success_at=None if connection is None else connection.last_success_at,
        checkpoint_date=None if connection is None else connection.checkpoint_date,
        capabilities={} if connection is None else connection.capabilities,
        last_error_code=None if connection is None else connection.last_error_code,
        client_version=None if connection is None else connection.client_version,
        latest_sync_status=None if latest is None else latest.status.value,
        latest_sync_warning_codes=[] if latest is None else latest.warning_codes,
    )


@router.post(
    "/sync",
    dependencies=[Depends(require_csrf)],
    status_code=202,
    response_model=GarminSyncRequestOut,
)
def request_sync(
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=120)],
    date_from: date | None = None,
    date_to: date | None = None,
    refresh: bool = False,
) -> GarminSyncRequestOut:
    connection = session.scalar(
        select(GarminConnection).where(GarminConnection.owner_id == owner.id)
    )
    if (
        not settings.garmin_enabled
        or connection is None
        or connection.state is not GarminConnectionState.CONNECTED
    ):
        raise HTTPException(status_code=409, detail={"code": "garmin_connection_not_enabled"})
    local_today = datetime.now(ZoneInfo(owner.default_timezone)).date()
    end = date_to or local_today
    start = date_from or (end - timedelta(days=settings.garmin_sync_lookback_days - 1))
    if end > local_today:
        raise HTTPException(status_code=422, detail={"code": "garmin_sync_future_window"})
    try:
        result = enqueue_sync(
            session,
            owner_id=owner.id,
            start_date=start,
            end_date=end,
            timezone=owner.default_timezone,
            idempotency_key=f"manual:{owner.id}:{idempotency_key}",
            force_refresh=refresh,
        )
    except JobQueueError as exc:
        if exc.reason_code == "garmin_sync_window_invalid":
            raise HTTPException(status_code=422, detail={"code": exc.reason_code}) from exc
        raise HTTPException(status_code=409, detail={"code": exc.reason_code}) from exc
    return GarminSyncRequestOut(
        job_id=result.job.id,
        status=result.job.status.value,
        disposition=result.disposition,
        requested_start_date=start,
        requested_end_date=end,
        cooldown_until=result.cooldown_until,
    )


@router.get("/records", response_model=GarminRecordsOut)
def records(session: DbSession, owner: CurrentOwner) -> GarminRecordsOut:
    output: list[GarminRecordOut] = []
    for model in (GarminMetricEvent, GarminSleepEvent, GarminActivityEvent):
        rows = list(session.scalars(select(model).where(model.owner_id == owner.id)))
        for row in events.current_only(session, model, rows):
            if isinstance(row, GarminMetricEvent):
                output.append(
                    GarminRecordOut(
                        id=row.id,
                        kind="daily",
                        summary=(
                            f"{row.metric_type.value.replace('_', ' ').title()}: "
                            f"{row.value} {row.unit}"
                        ),
                        time=time_out(row),
                        provenance=provenance_out(row),
                        metric_type=row.metric_type.value,
                        value=str(row.value),
                        unit=row.unit,
                    )
                )
            elif isinstance(row, GarminSleepEvent):
                output.append(
                    GarminRecordOut(
                        id=row.id,
                        kind="sleep",
                        summary="Sleep interval recorded by Garmin",
                        time=time_out(row),
                        provenance=provenance_out(row),
                        ended_at=row.ended_at,
                        duration_seconds=row.duration_seconds,
                        duration_source=row.garmin_duration_source,
                        awakenings=row.awakenings,
                        sleep_score=row.overall_sleep_score,
                    )
                )
            else:
                output.append(
                    GarminRecordOut(
                        id=row.id,
                        kind="activity",
                        summary=f"Garmin activity: {row.sport.replace('_', ' ')}",
                        time=time_out(row),
                        provenance=provenance_out(row),
                        ended_at=row.ended_at,
                        duration_seconds=(
                            None if row.elapsed_seconds is None else int(row.elapsed_seconds)
                        ),
                        activity_type=row.sport,
                        distance_miles=(
                            None if row.distance_miles is None else str(row.distance_miles)
                        ),
                    )
                )
    output.sort(key=lambda row: (row.time.occurred_at, row.kind, str(row.id)))
    return GarminRecordsOut(records=output)


@router.get("/disconnect-preview", response_model=GarminDisconnectPreviewOut)
def disconnect_preview(session: DbSession, owner: CurrentOwner) -> GarminDisconnectPreviewOut:
    connection = session.scalar(
        select(GarminConnection).where(GarminConnection.owner_id == owner.id)
    )

    def count(model: Any, automatic: bool) -> int:
        condition = (
            model.garmin_sync_run_id.is_not(None)
            if automatic
            else model.garmin_import_batch_id.is_not(None)
        )
        return (
            session.scalar(
                select(func.count()).select_from(model).where(model.owner_id == owner.id, condition)
            )
            or 0
        )

    return GarminDisconnectPreviewOut(
        state="not_connected" if connection is None else connection.state.value,
        automatic_fact_rows=sum(
            count(model, True)
            for model in (GarminMetricEvent, GarminSleepEvent, GarminActivityEvent)
        ),
        reviewed_import_fact_rows=sum(
            count(model, False)
            for model in (GarminMetricEvent, GarminSleepEvent, GarminActivityEvent)
        ),
        sync_run_rows=(
            session.scalar(
                select(func.count())
                .select_from(GarminSyncRun)
                .where(GarminSyncRun.owner_id == owner.id)
            )
            or 0
        ),
    )


async def _parse(file: UploadFile, timezone: str) -> ParsedGarminImport:
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        return parse_upload(file.filename, payload, timezone)
    except GarminImportError as exc:
        code = str(exc)
        http_status = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if code in {"file_too_large", "archive_expanded_too_large"}
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=http_status, detail={"code": code}) from exc
    finally:
        await file.close()


@router.post("/imports/preview", dependencies=[Depends(require_csrf)])
async def preview_import(
    owner: CurrentOwner,
    file: Annotated[UploadFile, File()],
    timezone: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Parse locally and return candidates; this endpoint creates no facts."""
    parsed = await _parse(file, timezone or owner.default_timezone)
    return _preview_payload(parsed)


@router.post("/imports/confirm", dependencies=[Depends(require_csrf)])
async def confirm_import_route(
    session: DbSession,
    owner: CurrentOwner,
    file: Annotated[UploadFile, File()],
    expected_sha256: Annotated[str, Form(min_length=64, max_length=64)],
    timezone: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Reparse and confirm an unchanged preview as immutable recorded facts."""
    parsed = await _parse(file, timezone or owner.default_timezone)
    if not hmac.compare_digest(parsed.source_sha256, expected_sha256.casefold()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "preview_checksum_mismatch"},
        )
    result = confirm_import(session, owner_id=owner.id, parsed=parsed)
    return {
        "batch_id": str(result.batch.id),
        "source_sha256": result.batch.source_sha256,
        "created": result.created,
        "metric_count": result.metric_count,
        "sleep_count": result.sleep_count,
        "activity_count": result.activity_count,
    }


def _preview_payload(parsed: ParsedGarminImport) -> dict[str, Any]:
    return {
        "creates_facts": False,
        "source_name": parsed.source_name,
        "source_sha256": parsed.source_sha256,
        "source_members": parsed.source_members,
        "sdk_profile_version": parsed.sdk_profile_version,
        "supported_metrics": sorted(SUPPORTED_METRICS),
        "observed_metrics": parsed.observed_metrics,
        "missing_metrics": parsed.missing_metrics,
        "device_attributions": parsed.device_attributions,
        "warnings": parsed.warnings,
        "candidates": [_candidate_payload(candidate) for candidate in parsed.candidates],
    }


def _candidate_payload(candidate: MetricCandidate | SleepCandidate | ActivityCandidate):
    assert candidate.time is not None and candidate.source is not None
    common: dict[str, Any] = {
        "kind": candidate.kind,
        "occurred_at": candidate.time.occurred_at,
        "local_time": candidate.time.local_time,
        "timezone": candidate.time.timezone,
        "source_member": candidate.source.member_name,
        "source_sha256": candidate.source.member_sha256,
        "device": candidate.source.device.as_dict(),
    }
    if isinstance(candidate, MetricCandidate):
        common.update(
            metric_type=candidate.metric_type.value,
            value=_decimal(candidate.value),
            unit=candidate.unit,
            field_name=candidate.field_name,
            period_end_at=candidate.period_end_at,
        )
    elif isinstance(candidate, SleepCandidate):
        common.update(
            ended_at=candidate.ended_at,
            overall_sleep_score=candidate.overall_sleep_score,
            stage_count=candidate.stage_count,
        )
    else:
        common.update(
            ended_at=candidate.ended_at,
            sport=candidate.sport,
            sub_sport=candidate.sub_sport,
            title=candidate.title,
            elapsed_seconds=_decimal(candidate.elapsed_seconds),
            distance_miles=_decimal(candidate.distance_miles),
            calories=candidate.calories,
            average_heart_rate=candidate.average_heart_rate,
            maximum_heart_rate=candidate.maximum_heart_rate,
        )
    return common


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
