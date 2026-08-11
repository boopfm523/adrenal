"""Owner-scoped deterministic data-quality findings."""

from fastapi import APIRouter
from pydantic import BaseModel

from healthcurve.api.deps import CurrentOwner, DbSession
from healthcurve.api.pagination import Pagination, page_metadata
from healthcurve.api.schemas import PageMetadata
from healthcurve.data_quality import findings_for_owner

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
            )
            for finding in visible
        ],
        page=metadata,
    )
