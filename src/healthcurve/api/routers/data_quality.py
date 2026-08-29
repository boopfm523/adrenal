"""Owner-scoped deterministic data-quality findings."""

import uuid
from datetime import datetime

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
from healthcurve.operations.jobs import Job, JobStatus, dead_letters

router = APIRouter(tags=["data-quality"])


class DataQualityFindingOut(BaseModel):
    id: str
    finding_kind: str
    severity: str
    source: str
    title: str
    detail: str
    record_id: str | None
    href: str | None
    action_label: str | None
    can_acknowledge: bool
    acknowledge_label: str | None
    occurred_at: datetime | None


class DataQualityOut(BaseModel):
    findings: list[DataQualityFindingOut]
    page: PageMetadata
    timezone: str
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
                acknowledge_label=finding.acknowledge_label,
                occurred_at=finding.occurred_at,
            )
            for finding in visible
        ],
        page=metadata,
        timezone=owner.default_timezone,
    )


def _already_acknowledged(
    session: DbSession,
    *,
    owner_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
) -> bool:
    return (
        session.scalar(
            select(AuditEntry.id).where(
                AuditEntry.actor == audit.actor_for_owner(owner_id),
                AuditEntry.action == audit.AuditAction.DATA_QUALITY_ACKNOWLEDGED,
                AuditEntry.target_type == target_type,
                AuditEntry.target_id == target_id,
            )
        )
        is not None
    )


def _record_acknowledgement(
    session: DbSession,
    *,
    owner_id: uuid.UUID,
    target_type: str,
    target_id: uuid.UUID,
    change_summary: str,
) -> None:
    if _already_acknowledged(
        session,
        owner_id=owner_id,
        target_type=target_type,
        target_id=target_id,
    ):
        return
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.DATA_QUALITY_ACKNOWLEDGED,
        target_type=target_type,
        target_id=target_id,
        change_summary=change_summary,
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
    _record_acknowledgement(
        session,
        owner_id=owner.id,
        target_type="garmin_sync_run",
        target_id=run.id,
        change_summary="reviewed Garmin sync warning notice",
    )


def _job_is_available_to_owner(job: Job, owner_id: uuid.UUID) -> bool:
    """Global operational jobs belong to this single-owner installation.

    Jobs that explicitly carry an owner remain owner-scoped even in tests or a
    future multi-owner deployment.
    """
    payload_owner = job.payload.get("owner_id")
    return payload_owner is None or str(payload_owner) == str(owner_id)


@router.post(
    "/data-quality/background-jobs/acknowledge-all",
    dependencies=[Depends(require_csrf)],
    status_code=status.HTTP_204_NO_CONTENT,
)
def acknowledge_all_background_job_findings(session: DbSession, owner: CurrentOwner) -> None:
    for job in dead_letters(session, owner_id=owner.id, limit=1000):
        _record_acknowledgement(
            session,
            owner_id=owner.id,
            target_type="background_job",
            target_id=job.id,
            change_summary="reviewed background job failure notice",
        )


@router.post(
    "/data-quality/background-jobs/{job_id}/acknowledge",
    dependencies=[Depends(require_csrf)],
    status_code=status.HTTP_204_NO_CONTENT,
)
def acknowledge_background_job_finding(
    job_id: uuid.UUID, session: DbSession, owner: CurrentOwner
) -> None:
    job = session.scalar(select(Job).where(Job.id == job_id, Job.status == JobStatus.DEAD_LETTER))
    if job is None or not _job_is_available_to_owner(job, owner.id):
        raise HTTPException(status_code=404, detail="data-quality finding not found")
    _record_acknowledgement(
        session,
        owner_id=owner.id,
        target_type="background_job",
        target_id=job.id,
        change_summary="reviewed background job failure notice",
    )
