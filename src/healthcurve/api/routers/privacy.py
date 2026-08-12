"""Re-authenticated privacy and deletion controls."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Header, HTTPException, Response, status
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from healthcurve import privacy
from healthcurve.api.deps import (
    AppRateLimiter,
    AppSettings,
    CurrentOwner,
    DbSession,
    enforce_rate_limit,
    require_csrf,
)
from healthcurve.api.pagination import Pagination, page_metadata
from healthcurve.api.schemas import PageMetadata
from healthcurve.identity import service as auth
from healthcurve.integrations.garmin.connect_jobs import enqueue_disconnect
from healthcurve.operations import audit
from healthcurve.operations.jobs import Job, JobStatus
from healthcurve.operations.rate_limit import RateLimitPolicy
from healthcurve.private_exports import service as exports
from healthcurve.private_exports.models import PrivateExport
from healthcurve.private_exports.storage import (
    PrivateExportStorageError,
    verified_path,
)

router = APIRouter(prefix="/privacy", tags=["privacy"])


class ReauthenticatedRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class IntegrationDeletionRequest(ReauthenticatedRequest):
    delete_data: bool = True
    confirmation: str | None = Field(default=None, max_length=80)


class AccountDeletionRequest(ReauthenticatedRequest):
    confirmation: str


class IntegrationDeletionResponse(BaseModel):
    credentials_deleted: int
    data_rows_deleted: int
    disconnect_requested: bool = False


class PrivateExportRequest(ReauthenticatedRequest):
    include_ai: bool = False
    include_sensitive: bool = True


class PrivateExportOut(BaseModel):
    id: uuid.UUID
    job_id: uuid.UUID
    status: Literal["queued", "running", "completed", "dead_letter", "expired"]
    include_ai: bool
    include_sensitive: bool
    processed_rows: int
    total_rows: int | None
    progress_percent: float | None
    attempt_count: int
    max_attempts: int
    next_attempt_at: datetime | None
    last_error_code: str | None
    created_at: datetime
    started_at: datetime | None
    completed_at: datetime | None
    expires_at: datetime
    download_url: str | None
    sha256: str | None
    byte_size: int | None


class PrivateExportPage(BaseModel):
    items: list[PrivateExportOut]
    page: PageMetadata


def _reauthenticate(owner: CurrentOwner, password: str) -> None:
    if not auth.verify_password(owner.password_hash, password):
        raise HTTPException(status_code=403, detail="password is incorrect")


def _export_out(export: PrivateExport, job: Job) -> PrivateExportOut:
    now = datetime.now(UTC)
    expired = export.purged_at is not None or export.expires_at <= now
    resolved_status: str = "expired" if expired else job.status.value
    downloadable = (
        not expired
        and job.status is JobStatus.COMPLETED
        and export.relative_path is not None
        and export.sha256 is not None
        and export.byte_size is not None
    )
    percent = None
    if export.total_rows is not None:
        percent = (
            100.0
            if export.total_rows == 0
            else round(export.processed_rows * 100 / export.total_rows, 1)
        )
    return PrivateExportOut(
        id=export.id,
        job_id=job.id,
        status=resolved_status,  # type: ignore[arg-type]
        include_ai=export.include_ai,
        include_sensitive=export.include_sensitive,
        processed_rows=export.processed_rows,
        total_rows=export.total_rows,
        progress_percent=percent,
        attempt_count=job.attempt_count,
        max_attempts=job.max_attempts,
        next_attempt_at=job.run_at if job.status is JobStatus.QUEUED else None,
        last_error_code=job.last_error_code,
        created_at=export.created_at,
        started_at=job.started_at,
        completed_at=export.completed_at,
        expires_at=export.expires_at,
        download_url=f"/api/v1/privacy/exports/{export.id}/download" if downloadable else None,
        sha256=export.sha256 if downloadable else None,
        byte_size=export.byte_size if downloadable else None,
    )


@router.post(
    "/export",
    response_model=PrivateExportOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
def private_export(
    payload: PrivateExportRequest,
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    limiter: AppRateLimiter,
    idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
) -> PrivateExportOut:
    _reauthenticate(owner, payload.password)
    try:
        requested = exports.request_export(
            session,
            owner_id=owner.id,
            idempotency_key=idempotency_key or "",
            include_ai=payload.include_ai,
            include_sensitive=payload.include_sensitive,
        )
    except exports.PrivateExportError as exc:
        code = 409 if exc.reason_code == "export_idempotency_options_conflict" else 422
        raise HTTPException(status_code=code, detail=exc.reason_code) from exc
    if not requested.replayed:
        enforce_rate_limit(
            response,
            limiter,
            scope="export",
            identity=str(owner.id),
            policy=RateLimitPolicy(settings.report_rate_limit, settings.report_rate_window_s),
        )
        audit.record(
            session,
            actor=audit.actor_for_owner(owner.id),
            action=audit.AuditAction.EXPORT_REQUESTED,
            target_type="private_export",
            target_id=requested.export.id,
            change_summary=(
                f"queued;ai={payload.include_ai};sensitive={payload.include_sensitive}"
            ),
        )
    return _export_out(requested.export, requested.job)


@router.get("/exports", response_model=PrivateExportPage)
def list_private_exports(
    session: DbSession, owner: CurrentOwner, pagination: Pagination
) -> PrivateExportPage:
    query = (
        select(PrivateExport, Job)
        .join(Job, Job.id == PrivateExport.job_id)
        .where(PrivateExport.owner_id == owner.id)
    )
    total_items = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    metadata = page_metadata(total_items, pagination)
    rows = session.execute(
        query.order_by(PrivateExport.created_at.desc(), PrivateExport.id.desc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    return PrivateExportPage(items=[_export_out(row[0], row[1]) for row in rows], page=metadata)


@router.get("/exports/{export_id}", response_model=PrivateExportOut)
def get_private_export(
    export_id: uuid.UUID, session: DbSession, owner: CurrentOwner
) -> PrivateExportOut:
    result = exports.owned_export(session, owner_id=owner.id, export_id=export_id)
    if result is None:
        raise HTTPException(status_code=404, detail="private export not found")
    return _export_out(*result)


@router.get("/exports/{export_id}/download")
def download_private_export(
    export_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> FileResponse:
    result = exports.owned_export(session, owner_id=owner.id, export_id=export_id)
    if result is None:
        raise HTTPException(status_code=404, detail="private export not found")
    export, job = result
    now = datetime.now(UTC)
    if (
        job.status is not JobStatus.COMPLETED
        or export.purged_at is not None
        or export.expires_at <= now
        or export.relative_path is None
        or export.sha256 is None
        or export.byte_size is None
    ):
        raise HTTPException(status_code=409, detail="private export is not available")
    try:
        path = verified_path(
            settings.report_artifacts_dir,
            relative_path=export.relative_path,
            expected_sha256=export.sha256,
            expected_size=export.byte_size,
        )
    except PrivateExportStorageError as exc:
        raise HTTPException(status_code=410, detail=exc.args[0]) from exc
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.EXPORT_DOWNLOADED,
        target_type="private_export",
        target_id=export.id,
        change_summary="private JSON artifact downloaded",
    )
    return FileResponse(
        path,
        media_type="application/json",
        filename=f"healthcurve-export-{export.created_at:%Y%m%d}.json",
        headers={"Cache-Control": "no-store", "X-Content-SHA256": export.sha256},
    )


@router.delete(
    "/records/{record_type}/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_record(
    record_type: str,
    record_id: uuid.UUID,
    payload: ReauthenticatedRequest,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> None:
    _reauthenticate(owner, payload.password)
    try:
        deleted = privacy.delete_record(
            session,
            owner_id=owner.id,
            record_type=record_type,
            record_id=record_id,
            uploads_dir=settings.uploads_dir,
        )
    except privacy.CorrectionHistoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except privacy.DeletionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="record not found")


@router.delete(
    "/integrations/{provider}",
    response_model=IntegrationDeletionResponse,
    dependencies=[Depends(require_csrf)],
)
def delete_integration(
    provider: str,
    payload: IntegrationDeletionRequest,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> IntegrationDeletionResponse:
    _reauthenticate(owner, payload.password)
    if provider == "garmin":
        expected = (
            "DISCONNECT GARMIN AND DELETE DATA" if payload.delete_data else "DISCONNECT GARMIN"
        )
        if payload.confirmation != expected:
            raise HTTPException(status_code=422, detail="confirmation phrase does not match")
    try:
        result = privacy.delete_integration(
            session,
            owner_id=owner.id,
            provider=provider,
            delete_data=payload.delete_data,
            telegram_chat_id=settings.telegram_allowed_chat_id,
        )
    except privacy.DeletionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.disconnect_requested:
        enqueue_disconnect(
            session,
            owner_id=owner.id,
            idempotency_key=f"disconnect:{owner.id}:{uuid.uuid4()}",
        )
    return IntegrationDeletionResponse(
        credentials_deleted=result.credentials,
        data_rows_deleted=result.data_rows,
        disconnect_requested=result.disconnect_requested,
    )


@router.delete(
    "/account",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_account(
    payload: AccountDeletionRequest,
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> None:
    _reauthenticate(owner, payload.password)
    if payload.confirmation != "DELETE MY HEALTHCURVE ACCOUNT":
        raise HTTPException(status_code=422, detail="confirmation phrase does not match")
    try:
        privacy.delete_account(
            session,
            owner=owner,
            uploads_dir=settings.uploads_dir,
            telegram_chat_id=settings.telegram_allowed_chat_id,
            report_artifacts_dir=settings.report_artifacts_dir,
        )
    except privacy.DeletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")
