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
from sqlalchemy import func, literal, select, union_all

from healthcurve.api.date_filters import local_date_window
from healthcurve.api.deps import AppSettings, CurrentOwner, DbSession, require_csrf
from healthcurve.api.pagination import Pagination, page_metadata, paginate_current_facts
from healthcurve.api.routers.events import provenance_out, time_out
from healthcurve.api.schemas import EventTimeOut, PageMetadata, ProvenanceOut
from healthcurve.context.models import ContextEvent
from healthcurve.integrations.garmin.connect_jobs import GarminSyncDisposition, enqueue_sync
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminConnection,
    GarminConnectionState,
    GarminMetricEvent,
    GarminSleepEvent,
    GarminSleepKind,
    GarminSleepStageInterval,
    GarminSyncOrigin,
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
from healthcurve.integrations.garmin.presentation import (
    aggregate_period_label,
    measurement_label,
    measurement_summary,
)
from healthcurve.integrations.garmin.service import confirm_import
from healthcurve.operations.jobs import JobQueueError

router = APIRouter(prefix="/integrations/garmin", tags=["garmin"])


class GarminStatusOut(BaseModel):
    configured: bool
    state: str
    automatic_sync_hour_local: int
    automatic_sync_interval_hours: int
    sync_lookback_days: int
    last_success_at: datetime | None = None
    checkpoint_date: date | None = None
    capabilities: dict[str, str] = Field(default_factory=dict)
    last_error_code: str | None = None
    client_version: str | None = None
    latest_sync_status: str | None = None
    latest_sync_origin: GarminSyncOrigin | None = None
    latest_sync_warning_codes: list[str] = Field(default_factory=list)


class GarminSleepIntervalOut(BaseModel):
    stage: Literal["awake"]
    started_at: datetime
    ended_at: datetime


class GarminActivityWeatherOut(BaseModel):
    observed_at: datetime
    interval_ended_at: datetime | None = None
    temperature_c: str | None = None
    apparent_temperature_c: str | None = None
    humidity_percent: str | None = None
    precipitation_mm: str | None = None
    conditions: str | None = None
    wind_speed_kph: str | None = None
    wind_gust_kph: str | None = None
    provider: str


class GarminRecordOut(BaseModel):
    id: uuid.UUID
    kind: Literal["daily", "sample", "sleep", "activity"]
    summary: str
    time: EventTimeOut
    provenance: ProvenanceOut
    metric_type: str | None = None
    value: str | None = None
    unit: str | None = None
    aggregation: str | None = None
    sample_interval_seconds: int | None = None
    garmin_field_name: str | None = None
    measurement_label: str | None = None
    period_label: str | None = None
    ended_at: datetime | None = None
    duration_seconds: int | None = None
    duration_source: str | None = None
    awakenings: int | None = None
    sleep_score: int | None = None
    sleep_kind: Literal["overnight", "nap"] | None = None
    sleep_intervals: list[GarminSleepIntervalOut] = Field(default_factory=list)
    activity_type: str | None = None
    activity_environment: str | None = None
    activity_location_name: str | None = None
    activity_weather: GarminActivityWeatherOut | None = None
    distance_miles: str | None = None


class GarminRecordsOut(BaseModel):
    records: list[GarminRecordOut]
    page: PageMetadata
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
    origin: GarminSyncOrigin
    requested_start_date: date
    requested_end_date: date
    cooldown_until: datetime | None = None


class GarminSettingsIn(BaseModel):
    sync_lookback_days: int = Field(ge=1, le=31)


class GarminSettingsOut(BaseModel):
    sync_lookback_days: int


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
        automatic_sync_hour_local=settings.garmin_sync_hour_local,
        automatic_sync_interval_hours=settings.garmin_sync_interval_hours,
        sync_lookback_days=(
            settings.garmin_sync_lookback_days
            if connection is None
            else connection.sync_lookback_days
        ),
        last_success_at=None if connection is None else connection.last_success_at,
        checkpoint_date=None if connection is None else connection.checkpoint_date,
        capabilities={} if connection is None else connection.capabilities,
        last_error_code=None if connection is None else connection.last_error_code,
        client_version=None if connection is None else connection.client_version,
        latest_sync_status=None if latest is None else latest.status.value,
        latest_sync_origin=None if latest is None else latest.origin,
        latest_sync_warning_codes=[] if latest is None else latest.warning_codes,
    )


@router.patch(
    "/settings",
    dependencies=[Depends(require_csrf)],
    response_model=GarminSettingsOut,
)
def update_settings(
    payload: GarminSettingsIn, session: DbSession, owner: CurrentOwner
) -> GarminSettingsOut:
    connection = session.scalar(
        select(GarminConnection).where(GarminConnection.owner_id == owner.id).with_for_update()
    )
    if connection is None:
        raise HTTPException(status_code=409, detail={"code": "garmin_connection_not_enabled"})
    connection.sync_lookback_days = payload.sync_lookback_days
    session.flush()
    return GarminSettingsOut(sync_lookback_days=connection.sync_lookback_days)


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
    start = date_from or (end - timedelta(days=connection.sync_lookback_days - 1))
    if end > local_today:
        raise HTTPException(status_code=422, detail={"code": "garmin_sync_future_window"})
    try:
        origin = GarminSyncOrigin.MANUAL_REFRESH if refresh else GarminSyncOrigin.MANUAL
        result = enqueue_sync(
            session,
            owner_id=owner.id,
            start_date=start,
            end_date=end,
            timezone=owner.default_timezone,
            idempotency_key=f"manual:{owner.id}:{idempotency_key}",
            force_refresh=refresh,
            origin=origin,
        )
    except JobQueueError as exc:
        if exc.reason_code == "garmin_sync_window_invalid":
            raise HTTPException(status_code=422, detail={"code": exc.reason_code}) from exc
        raise HTTPException(status_code=409, detail={"code": exc.reason_code}) from exc
    return GarminSyncRequestOut(
        job_id=result.job.id,
        status=result.job.status.value,
        disposition=result.disposition,
        origin=GarminSyncOrigin(
            str(result.job.payload.get("origin", GarminSyncOrigin.LEGACY.value))
        ),
        requested_start_date=start,
        requested_end_date=end,
        cooldown_until=result.cooldown_until,
    )


@router.get("/records", response_model=GarminRecordsOut)
def records(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
) -> GarminRecordsOut:
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    models = (
        ("daily", GarminMetricEvent),
        ("sleep", GarminSleepEvent),
        ("activity", GarminActivityEvent),
    )
    current = []
    for kind, model in models:
        superseded = select(model.supersedes_id).where(
            model.owner_id == owner.id, model.supersedes_id.is_not(None)
        )
        query = select(
            literal(kind).label("kind"),
            model.id.label("id"),
            model.occurred_at.label("occurred_at"),
        ).where(model.owner_id == owner.id, model.id.not_in(superseded))
        if model is GarminMetricEvent:
            query = query.where(GarminMetricEvent.aggregation != "provider_sample")
        if window.start is not None:
            query = query.where(model.occurred_at >= window.start)
        if window.end_exclusive is not None:
            query = query.where(model.occurred_at < window.end_exclusive)
        current.append(query)
    combined = union_all(*current).subquery()
    total_items = session.scalar(select(func.count()).select_from(combined)) or 0
    metadata = page_metadata(total_items, pagination)
    visible = session.execute(
        select(combined.c.kind, combined.c.id, combined.c.occurred_at)
        .order_by(combined.c.occurred_at.desc(), combined.c.kind, combined.c.id)
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    ids_by_kind = {kind: {row.id for row in visible if row.kind == kind} for kind, _model in models}
    records_by_key: dict[tuple[str, uuid.UUID], GarminRecordOut] = {}
    for kind, model in models:
        for row in session.scalars(select(model).where(model.id.in_(ids_by_kind[kind]))):
            records_by_key[(kind, row.id)] = _garmin_record_out(row, session=session)
    return GarminRecordsOut(
        records=[records_by_key[(row.kind, row.id)] for row in visible],
        page=metadata,
    )


@router.get("/samples", response_model=GarminRecordsOut)
def samples(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    day: date,
    timezone: str | None = None,
) -> GarminRecordsOut:
    """Return one bounded local day of current intraday Garmin samples."""

    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=day,
        date_to=day,
    )
    if window.start is None or window.end_exclusive is None:  # pragma: no cover
        raise AssertionError("single-day Garmin sample window must be bounded")
    page = paginate_current_facts(
        session,
        GarminMetricEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=(
            GarminMetricEvent.aggregation == "provider_sample",
            GarminMetricEvent.occurred_at >= window.start,
            GarminMetricEvent.occurred_at < window.end_exclusive,
        ),
        include_revisions=False,
    )
    return GarminRecordsOut(
        records=[_garmin_record_out(row) for row in page.items],
        page=page.metadata,
    )


@router.get("/sleep", response_model=GarminRecordsOut)
def list_sleep_records(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    day: date,
    timezone: str | None = None,
) -> GarminRecordsOut:
    """Return current sleep sessions that overlap one bounded local day."""

    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=day,
        date_to=day,
    )
    if window.start is None or window.end_exclusive is None:  # pragma: no cover
        raise AssertionError("single-day Garmin sleep window must be bounded")
    page = paginate_current_facts(
        session,
        GarminSleepEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=(
            GarminSleepEvent.occurred_at < window.end_exclusive,
            GarminSleepEvent.ended_at > window.start,
        ),
        include_revisions=False,
    )
    return GarminRecordsOut(
        records=[_garmin_record_out(row) for row in page.items],
        page=page.metadata,
    )


def _garmin_record_out(
    row: GarminMetricEvent | GarminSleepEvent | GarminActivityEvent,
    *,
    session: DbSession | None = None,
) -> GarminRecordOut:
    if isinstance(row, GarminMetricEvent):
        label = measurement_label(row.metric_type, row.garmin_field_name)
        return GarminRecordOut(
            id=row.id,
            kind="sample" if row.aggregation == "provider_sample" else "daily",
            summary=measurement_summary(
                row.metric_type,
                row.garmin_field_name,
                row.value,
                row.unit,
            ),
            time=time_out(row),
            provenance=provenance_out(row),
            metric_type=row.metric_type.value,
            value=str(row.value),
            unit=row.unit,
            aggregation=row.aggregation,
            sample_interval_seconds=row.sample_interval_seconds,
            garmin_field_name=row.garmin_field_name,
            measurement_label=label,
            period_label=(
                aggregate_period_label(row.garmin_field_name)
                if row.aggregation != "provider_sample"
                else None
            ),
        )
    if isinstance(row, GarminSleepEvent):
        is_nap = row.sleep_kind is GarminSleepKind.NAP
        return GarminRecordOut(
            id=row.id,
            kind="sleep",
            summary="Nap recorded by Garmin" if is_nap else "Sleep interval recorded by Garmin",
            time=time_out(row),
            provenance=provenance_out(row),
            ended_at=row.ended_at,
            duration_seconds=row.duration_seconds,
            duration_source=row.garmin_duration_source,
            awakenings=row.awakenings,
            sleep_score=row.overall_sleep_score,
            sleep_kind=row.sleep_kind.value,
            sleep_intervals=[
                GarminSleepIntervalOut(
                    stage=interval.stage.value,
                    started_at=interval.started_at,
                    ended_at=interval.ended_at,
                )
                for interval in row.stage_intervals
            ],
        )
    weather = None if session is None else _activity_weather(session, row)
    return GarminRecordOut(
        id=row.id,
        kind="activity",
        summary=f"Garmin activity: {row.sport.replace('_', ' ')}",
        time=time_out(row),
        provenance=provenance_out(row),
        ended_at=row.ended_at,
        duration_seconds=None if row.elapsed_seconds is None else int(row.elapsed_seconds),
        activity_type=row.sport,
        activity_environment=row.environment,
        activity_location_name=row.location_name,
        activity_weather=weather,
        distance_miles=None if row.distance_miles is None else str(row.distance_miles),
    )


def _activity_weather(
    session: DbSession, activity: GarminActivityEvent
) -> GarminActivityWeatherOut | None:
    row = session.scalar(
        select(ContextEvent)
        .where(
            ContextEvent.owner_id == activity.owner_id,
            ContextEvent.provider_id == f"open-meteo:garmin-activity:{activity.id}",
            ContextEvent.weather_provider == "open-meteo",
        )
        .order_by(ContextEvent.recorded_at.desc(), ContextEvent.id.desc())
        .limit(1)
    )
    if row is None or row.weather_observed_at is None or row.weather_provider is None:
        return None
    return GarminActivityWeatherOut(
        observed_at=row.weather_observed_at,
        interval_ended_at=row.weather_interval_ended_at,
        temperature_c=None if row.temperature is None else str(row.temperature),
        apparent_temperature_c=(
            None if row.apparent_temperature is None else str(row.apparent_temperature)
        ),
        humidity_percent=None if row.humidity_percent is None else str(row.humidity_percent),
        precipitation_mm=None if row.precipitation is None else str(row.precipitation),
        conditions=row.conditions,
        wind_speed_kph=None if row.wind_speed_kph is None else str(row.wind_speed_kph),
        wind_gust_kph=None if row.wind_gust_kph is None else str(row.wind_gust_kph),
        provider=row.weather_provider,
    )


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

    def sleep_interval_count(automatic: bool) -> int:
        source_condition = (
            GarminSleepEvent.garmin_sync_run_id.is_not(None)
            if automatic
            else GarminSleepEvent.garmin_import_batch_id.is_not(None)
        )
        return (
            session.scalar(
                select(func.count())
                .select_from(GarminSleepStageInterval)
                .join(
                    GarminSleepEvent,
                    GarminSleepEvent.id == GarminSleepStageInterval.sleep_event_id,
                )
                .where(GarminSleepEvent.owner_id == owner.id, source_condition)
            )
            or 0
        )

    return GarminDisconnectPreviewOut(
        state="not_connected" if connection is None else connection.state.value,
        automatic_fact_rows=sum(
            count(model, True)
            for model in (GarminMetricEvent, GarminSleepEvent, GarminActivityEvent)
        )
        + sleep_interval_count(True),
        reviewed_import_fact_rows=sum(
            count(model, False)
            for model in (GarminMetricEvent, GarminSleepEvent, GarminActivityEvent)
        )
        + sleep_interval_count(False),
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
