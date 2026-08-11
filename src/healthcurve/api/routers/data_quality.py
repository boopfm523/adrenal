"""Owner-scoped deterministic data-quality findings."""

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import select

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.pagination import Pagination, page_metadata
from healthcurve.api.schemas import PageMetadata
from healthcurve.data_quality import findings_for_owner
from healthcurve.integrations.garmin.models import GarminSyncRun
from healthcurve.operations import audit
from healthcurve.operations.audit import AuditEntry

router = APIRouter(tags=["data-quality"])


class DataQualityFindingOut(BaseModel):
    id: str
    finding_kind: str
    severity: str
    source: str
    title: str
    detail: str
    record_id: str | None
    href: str
    action_label: str
    can_acknowledge: bool


class DataQualityOut(BaseModel):
    findings: list[DataQualityFindingOut]
    page: PageMetadata
    completeness_notice: str = (
        "No known findings does not mean the health record is clinically complete."
    )


@router.get("/data-quality", response_model=DataQualityOut)
def data_quality(session: DbSession, owner: CurrentOwner, pagination: Pagination) -> DataQualityOut:
    findings = findings_for_owner(session, owner.id)
    metadata = page_metadata(len(findings), pagination)
    visible = findings[pagination.offset : pagination.offset + pagination.page_size]
    return DataQualityOut(
        findings=[
            DataQualityFindingOut(
                id=finding.id,
                finding_kind=finding.finding_kind,
                severity=finding.severity,
                source=finding.source,
                title=finding.title,
                detail=finding.detail,
                record_id=None if finding.record_id is None else str(finding.record_id),
                href=finding.href,
                action_label=finding.action_label,
                can_acknowledge=finding.can_acknowledge,
            )
            for finding in visible
        ],
        page=metadata,
    )


@router.post(
    "/data-quality/garmin-syncs/{sync_run_id}/acknowledge",
    dependencies=[Depends(require_csrf)],
    status_code=status.HTTP_204_NO_CONTENT,
)
def acknowledge_garmin_sync_finding(
    sync_run_id: uuid.UUID, session: DbSession, owner: CurrentOwner
) -> None:
    run = session.scalar(
        select(GarminSyncRun).where(
            GarminSyncRun.id == sync_run_id,
            GarminSyncRun.owner_id == owner.id,
        )
    )
    if run is None or not run.warning_codes:
        raise HTTPException(status_code=404, detail="data-quality finding not found")
    existing = session.scalar(
        select(AuditEntry.id).where(
            AuditEntry.actor == audit.actor_for_owner(owner.id),
            AuditEntry.action == audit.AuditAction.DATA_QUALITY_ACKNOWLEDGED,
            AuditEntry.target_type == "garmin_sync_run",
            AuditEntry.target_id == run.id,
        )
    )
    if existing is None:
        audit.record(
            session,
            actor=audit.actor_for_owner(owner.id),
            action=audit.AuditAction.DATA_QUALITY_ACKNOWLEDGED,
            target_type="garmin_sync_run",
            target_id=run.id,
            change_summary="reviewed Garmin sync warning notice",
        )
