"""Authenticated physician-report generation, snapshots, and private downloads."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from healthcurve.api.date_filters import local_date_window
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
from healthcurve.operations import audit
from healthcurve.operations.rate_limit import RateLimitPolicy
from healthcurve.reports import builder, rendering, storage
from healthcurve.reports.models import ReportArtifact, ReportSnapshot
from healthcurve.reports.service import SnapshotValidationError, document

router = APIRouter(prefix="/reports", tags=["reports"])
MAX_RANGE_DAYS = 366


class ReportCreateRequest(BaseModel):
    date_from: date
    date_to: date
    timezone: str | None = None
    selected_sections: list[str] = Field(min_length=1, max_length=len(builder.SUPPORTED_SECTIONS))
    include_ai: bool = False
    include_sensitive: bool = False
    companion_formats: set[Literal["csv", "json"]] = Field(default_factory=set)


class ReportArtifactOut(BaseModel):
    format: str
    media_type: str
    sha256: str
    byte_size: int
    download_url: str


class ReportOut(BaseModel):
    id: uuid.UUID
    date_from: date
    date_to: date
    timezone: str
    selected_sections: list[str]
    include_ai: bool
    canonical_sha256: str
    render_version: str
    created_at: datetime
    artifacts: list[ReportArtifactOut]


class ReportPreviewOut(ReportOut):
    source_manifest: dict[str, list[str]]
    metric_values: dict[str, object]
    snapshot_content: dict[str, object]


class ReportPage(BaseModel):
    items: list[ReportOut]
    page: PageMetadata


def _artifact_out(artifact: ReportArtifact) -> ReportArtifactOut:
    return ReportArtifactOut(
        format=artifact.format,
        media_type=artifact.media_type,
        sha256=artifact.sha256,
        byte_size=artifact.byte_size,
        download_url=f"/api/v1/reports/{artifact.snapshot_id}/artifacts/{artifact.format}",
    )


def _report_out(snapshot: ReportSnapshot, artifacts: list[ReportArtifact]) -> ReportOut:
    return ReportOut(
        id=snapshot.id,
        date_from=snapshot.date_from,
        date_to=snapshot.date_to,
        timezone=snapshot.timezone,
        selected_sections=snapshot.selected_sections,
        include_ai=snapshot.include_ai,
        canonical_sha256=snapshot.canonical_sha256,
        render_version=snapshot.render_version,
        created_at=snapshot.created_at,
        artifacts=[
            _artifact_out(artifact) for artifact in sorted(artifacts, key=lambda row: row.format)
        ],
    )


def _owned_snapshot(
    session: DbSession, owner: CurrentOwner, snapshot_id: uuid.UUID
) -> ReportSnapshot:
    snapshot = session.scalar(
        select(ReportSnapshot).where(
            ReportSnapshot.id == snapshot_id, ReportSnapshot.owner_id == owner.id
        )
    )
    if snapshot is None:
        raise HTTPException(status_code=404, detail="report not found")
    return snapshot


def _artifacts(
    session: DbSession, owner_id: uuid.UUID, snapshot_id: uuid.UUID
) -> list[ReportArtifact]:
    return list(
        session.scalars(
            select(ReportArtifact).where(
                ReportArtifact.owner_id == owner_id, ReportArtifact.snapshot_id == snapshot_id
            )
        )
    )


@router.post(
    "",
    response_model=ReportOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_report(
    payload: ReportCreateRequest,
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    limiter: AppRateLimiter,
) -> ReportOut:
    enforce_rate_limit(
        response,
        limiter,
        scope="report",
        identity=str(owner.id),
        policy=RateLimitPolicy(settings.report_rate_limit, settings.report_rate_window_s),
    )
    zone_name = payload.timezone or owner.default_timezone
    try:
        ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid timezone") from exc
    if payload.date_to < payload.date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")
    if (payload.date_to - payload.date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=f"range cannot exceed {MAX_RANGE_DAYS} days")
    selected = payload.selected_sections
    if len(set(selected)) != len(selected):
        raise HTTPException(status_code=422, detail="selected sections must be unique")
    unsupported = sorted(set(selected) - builder.SUPPORTED_SECTIONS)
    if unsupported:
        raise HTTPException(
            status_code=422, detail=f"unsupported report sections: {', '.join(unsupported)}"
        )
    try:
        snapshot = builder.build_snapshot(
            session,
            owner_id=owner.id,
            date_from=payload.date_from,
            date_to=payload.date_to,
            timezone=zone_name,
            selected_sections=selected,
            include_ai=payload.include_ai,
            include_sensitive=payload.include_sensitive,
        )
        session.flush()
        bundle = rendering.render(snapshot)
        artifacts = storage.store(
            session,
            root=settings.report_artifacts_dir,
            snapshot=snapshot,
            rendered=bundle,
            companion_formats=set(payload.companion_formats),
        )
        session.flush()
    except (SnapshotValidationError, storage.ArtifactStorageError) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.REPORT_GENERATED,
        target_type="report_snapshot",
        target_id=snapshot.id,
        change_summary=(
            f"sections={len(selected)};ai={payload.include_ai};"
            f"formats={','.join(sorted({'pdf', *payload.companion_formats}))}"
        ),
    )
    return _report_out(snapshot, artifacts)


@router.get("", response_model=ReportPage)
def list_reports(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
) -> ReportPage:
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    query = select(ReportSnapshot).where(ReportSnapshot.owner_id == owner.id)
    if window.start is not None:
        query = query.where(ReportSnapshot.created_at >= window.start)
    if window.end_exclusive is not None:
        query = query.where(ReportSnapshot.created_at < window.end_exclusive)
    total_items = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    metadata = page_metadata(total_items, pagination)
    snapshots = list(
        session.scalars(
            query.order_by(ReportSnapshot.created_at.desc(), ReportSnapshot.id.desc())
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    )
    return ReportPage(
        items=[
            _report_out(snapshot, _artifacts(session, owner.id, snapshot.id))
            for snapshot in snapshots
        ],
        page=metadata,
    )


@router.get("/{snapshot_id}", response_model=ReportPreviewOut)
def get_report(snapshot_id: uuid.UUID, session: DbSession, owner: CurrentOwner) -> ReportPreviewOut:
    snapshot = _owned_snapshot(session, owner, snapshot_id)
    payload = document(snapshot)
    summary = _report_out(snapshot, _artifacts(session, owner.id, snapshot.id))
    return ReportPreviewOut(
        **summary.model_dump(),
        source_manifest=payload["source_manifest"],
        metric_values=payload["metric_values"],
        snapshot_content=payload["snapshot_content"],
    )


@router.get("/{snapshot_id}/artifacts/{format_name}")
def download_report(
    snapshot_id: uuid.UUID,
    format_name: Literal["pdf", "csv", "json"],
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> Response:
    _owned_snapshot(session, owner, snapshot_id)
    artifact = session.scalar(
        select(ReportArtifact).where(
            ReportArtifact.snapshot_id == snapshot_id,
            ReportArtifact.owner_id == owner.id,
            ReportArtifact.format == format_name,
        )
    )
    if artifact is None:
        raise HTTPException(status_code=404, detail="report artifact not found")
    try:
        payload = storage.read(settings.report_artifacts_dir, artifact)
    except storage.ArtifactStorageError as exc:
        raise HTTPException(status_code=503, detail="report artifact is unavailable") from exc
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.REPORT_DOWNLOADED,
        target_type="report_snapshot",
        target_id=snapshot_id,
        change_summary=f"format={format_name}",
    )
    return Response(
        content=payload,
        media_type=artifact.media_type,
        headers={
            "Content-Disposition": (
                f'attachment; filename="healthcurve-report-{snapshot_id}.{format_name}"'
            ),
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )
