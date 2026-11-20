"""Read-only access to imported health records and their AI opt-ins (ADR-0040).

Nothing here changes an imported record. The only write is the owner's per-category
chat/AI opt-in, which is CSRF-protected and audited. Original files are served from
the read-only dataset mount, only from paths that resolve inside it, and always as
downloads so imported HTML is never rendered in the application's origin.
"""

from __future__ import annotations

import hashlib
import uuid
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from fastapi.responses import FileResponse
from pydantic import Field
from sqlalchemy import func, select

from healthcurve.api.deps import (
    AppRateLimiter,
    AppSettings,
    CurrentOwner,
    DbSession,
    enforce_rate_limit,
    require_csrf,
)
from healthcurve.api.pagination import Pagination, page_metadata
from healthcurve.api.schemas import ApiModel, FactResource, PageMetadata
from healthcurve.integrations.health_records import visibility
from healthcurve.integrations.health_records.models import (
    AiVisibilityCategory,
    DatasetTable,
    HealthRecord,
    HealthRecordImportBatch,
    HealthRecordRevision,
    RecordState,
)
from healthcurve.integrations.health_records.reader import DatasetError, read_snapshot
from healthcurve.integrations.health_records.service import import_snapshot
from healthcurve.operations.rate_limit import RateLimitPolicy

router = APIRouter(prefix="/health-records", tags=["health-records"])

#: Original-file types the collector writes. Anything else is not served.
_MEDIA_TYPES = {
    ".pdf": "application/pdf",
    ".html": "text/html",
    ".htm": "text/html",
    ".md": "text/markdown",
    ".txt": "text/plain",
}

_CATEGORY_LABELS: dict[AiVisibilityCategory, str] = {
    AiVisibilityCategory.LABS: "Lab results",
    AiVisibilityCategory.IMAGING_IMPRESSIONS: "Imaging impressions",
    AiVisibilityCategory.IMAGING_REPORTS: "Full imaging reports",
    AiVisibilityCategory.ENCOUNTERS: "Visits",
    AiVisibilityCategory.CONDITIONS: "Conditions and problem lists",
    AiVisibilityCategory.IMMUNIZATIONS: "Immunizations",
    AiVisibilityCategory.ALLERGIES: "Allergies",
    AiVisibilityCategory.VITALS: "Portal vitals",
    AiVisibilityCategory.SURGICAL_HISTORY: "Surgical history",
    AiVisibilityCategory.CLINICAL_NOTES: "Clinical notes, discharge and procedure notes",
    AiVisibilityCategory.VISIT_SUMMARIES: "After Visit Summaries",
    AiVisibilityCategory.LETTERS_AND_ADMIN: "Letters, consents, and patient education",
    AiVisibilityCategory.OTHER_DOCUMENTS: (
        "Other documents (test reports, pathology, health summaries, family history)"
    ),
}

#: Only these categories have a chat/AI path today; the rest are stored for later.
_CATEGORIES_WITH_AI_PATH = frozenset({AiVisibilityCategory.LABS})


class HealthRecordOut(FactResource):
    id: uuid.UUID
    dataset_table: DatasetTable
    record_id: str
    title: str
    category_label: str | None
    kind: str | None
    record_date: date | None
    date_text: str | None
    local_time: datetime | None
    timezone: str | None
    timezone_assumed: bool
    organization: str = Field(description="The health system that produced it (shown as 'from')")
    source: str = Field(description="The channel it came through, kept for audit")
    state: RecordState
    ai_category: AiVisibilityCategory | None
    provider: str | None
    ordered_by: str | None
    reading_provider: str | None
    performing_lab: str | None
    prescriber: str | None
    summary_text: str | None
    has_full_text: bool
    file_count: int
    revision: int


class HealthRecordRevisionOut(ApiModel):
    revision: int
    batch_id: uuid.UUID
    content_sha256: str
    created_at: datetime


class HealthRecordDetailOut(HealthRecordOut):
    body_text: str | None
    file_paths: list[str]
    revisions: list[HealthRecordRevisionOut]


class HealthRecordPage(ApiModel):
    items: list[HealthRecordOut]
    page: PageMetadata


class HealthRecordBatchOut(ApiModel):
    id: uuid.UUID
    dataset_schema_version: int
    dataset_generated_at: str
    source_exported_at: dict[str, str]
    started_at: datetime
    completed_at: datetime
    counts: dict[str, dict[str, int]]


class HealthRecordBatchPage(ApiModel):
    items: list[HealthRecordBatchOut]
    page: PageMetadata


class AiVisibilityOut(ApiModel):
    category: AiVisibilityCategory
    label: str
    enabled: bool
    has_ai_path: bool = Field(
        description="False when no chat or AI feature reads this category yet"
    )


class AiVisibilityPage(ApiModel):
    items: list[AiVisibilityOut]
    medications_note: str


class AiVisibilityIn(ApiModel):
    enabled: bool


def _out(envelope: HealthRecord, revision: HealthRecordRevision) -> dict[str, object]:
    return {
        "id": envelope.id,
        "dataset_table": envelope.dataset_table,
        "record_id": envelope.record_id,
        "title": envelope.title,
        "category_label": envelope.category,
        "kind": envelope.kind,
        "record_date": envelope.record_date,
        "date_text": envelope.date_text,
        "local_time": envelope.local_time,
        "timezone": envelope.timezone,
        "timezone_assumed": envelope.timezone_assumed,
        "organization": envelope.organization,
        "source": envelope.source,
        "state": envelope.state,
        "ai_category": envelope.ai_category,
        "provider": revision.provider,
        "ordered_by": revision.ordered_by,
        "reading_provider": revision.reading_provider,
        "performing_lab": revision.performing_lab,
        "prescriber": revision.prescriber,
        "summary_text": revision.summary_text,
        "has_full_text": bool(revision.body_text),
        "file_count": len(revision.file_paths or []),
        "revision": envelope.current_revision,
    }


def _current_revision_join():
    return (HealthRecordRevision.health_record_id == HealthRecord.id) & (
        HealthRecordRevision.revision == HealthRecord.current_revision
    )


@router.get("", response_model=HealthRecordPage)
def list_health_records(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    dataset_table: DatasetTable | None = None,
    organization_key: Annotated[
        str | None,
        Query(
            pattern="^[0-9a-f]{16}$",
            description="Opaque key from /health-records/organizations; names stay out of URLs",
        ),
    ] = None,
    state: RecordState | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    sort_order: Literal["asc", "desc"] = "desc",
) -> HealthRecordPage:
    """Imported records, newest source date first. Dates are the source's own dates."""
    query = (
        select(HealthRecord, HealthRecordRevision)
        .join(HealthRecordRevision, _current_revision_join())
        .where(HealthRecord.owner_id == owner.id)
    )
    if dataset_table is not None:
        query = query.where(HealthRecord.dataset_table == dataset_table)
    if organization_key:
        names = [
            name
            for name in _organization_names(session, owner.id)
            if organization_key_for(owner.id, name) == organization_key
        ]
        query = query.where(HealthRecord.organization.in_(names))
    if state is not None:
        query = query.where(HealthRecord.state == state)
    if date_from is not None:
        query = query.where(HealthRecord.record_date >= date_from)
    if date_to is not None:
        query = query.where(HealthRecord.record_date <= date_to)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    metadata = page_metadata(total, pagination)
    date_order = (
        HealthRecord.record_date.asc().nulls_last()
        if sort_order == "asc"
        else HealthRecord.record_date.desc().nulls_last()
    )
    rows = session.execute(
        query.order_by(date_order, HealthRecord.title, HealthRecord.id)
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).tuples()
    return HealthRecordPage(
        items=[HealthRecordOut.model_validate(_out(env, rev)) for env, rev in rows],
        page=metadata,
    )


def organization_key_for(owner_id: uuid.UUID, organization: str) -> str:
    """A per-owner opaque key, so filtering never puts a health system's name in a URL
    or an access log (where it would reveal where the owner receives care)."""
    return hashlib.sha256(f"{owner_id}:{organization}".encode()).hexdigest()[:16]


def _organization_names(session: DbSession, owner_id: uuid.UUID) -> list[str]:
    return list(
        session.scalars(
            select(HealthRecord.organization)
            .where(HealthRecord.owner_id == owner_id)
            .distinct()
            .order_by(HealthRecord.organization)
        )
    )


class OrganizationOut(ApiModel):
    key: str
    name: str


@router.get("/organizations", response_model=list[OrganizationOut])
def health_record_organizations(session: DbSession, owner: CurrentOwner) -> list[OrganizationOut]:
    """Distinct producing organizations with opaque keys for the "from" filter."""
    return [
        OrganizationOut(key=organization_key_for(owner.id, name), name=name)
        for name in _organization_names(session, owner.id)
    ]


@router.get("/batches", response_model=HealthRecordBatchPage)
def list_health_record_batches(
    session: DbSession, owner: CurrentOwner, pagination: Pagination
) -> HealthRecordBatchPage:
    query = select(HealthRecordImportBatch).where(HealthRecordImportBatch.owner_id == owner.id)
    total = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = session.scalars(
        query.order_by(HealthRecordImportBatch.completed_at.desc(), HealthRecordImportBatch.id)
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    return HealthRecordBatchPage(
        items=[HealthRecordBatchOut.model_validate(row, from_attributes=True) for row in rows],
        page=page_metadata(total, pagination),
    )


class ImportTableCounts(ApiModel):
    table: DatasetTable
    new: int
    changed: int
    unchanged: int
    no_longer_in_source: int
    returned: int


class ImportRunOut(ApiModel):
    dry_run: bool
    batch_id: uuid.UUID | None
    dataset_generated_at: str
    source_exported_at: dict[str, str]
    tables: list[ImportTableCounts]
    lab_panels_created: int
    lab_panels_corrected: int


#: Each run reads and diffs the whole snapshot (seconds). This bounds accidental
#: repeat clicks without getting in the way of re-importing after a rebuild.
IMPORT_RATE_POLICY = RateLimitPolicy(30, 3600)


@router.post(
    "/imports",
    response_model=ImportRunOut,
    dependencies=[Depends(require_csrf)],
)
def run_health_record_import(
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    limiter: AppRateLimiter,
    dry_run: bool = False,
) -> ImportRunOut:
    """Import (or, with dry_run, only diff) the read-only dataset snapshot now."""
    if settings.health_records_root is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="The health-records dataset is not configured (HC_HEALTH_RECORDS_DIR).",
        )
    enforce_rate_limit(
        response,
        limiter,
        scope="health_records_import",
        identity=str(owner.id),
        policy=IMPORT_RATE_POLICY,
    )
    started = datetime.now(UTC)
    try:
        snapshot = read_snapshot(settings.health_records_root)
    except DatasetError as exc:
        # Messages name only tables, columns, and versions, never record contents.
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"Import refused; nothing was written: {exc}",
        ) from exc
    result = import_snapshot(
        session, owner_id=owner.id, snapshot=snapshot, dry_run=dry_run, started_at=started
    )
    if dry_run:
        session.rollback()
    else:
        session.commit()
    return ImportRunOut(
        dry_run=dry_run,
        batch_id=result.batch_id,
        dataset_generated_at=snapshot.generated_at,
        source_exported_at=snapshot.source_exported_at,
        tables=[
            ImportTableCounts(table=table, **counts.as_dict())
            for table, counts in result.tables.items()
        ],
        lab_panels_created=result.labs.panels_created,
        lab_panels_corrected=result.labs.panels_corrected,
    )


@router.get("/ai-visibility", response_model=AiVisibilityPage)
def get_ai_visibility(session: DbSession, owner: CurrentOwner) -> AiVisibilityPage:
    enabled = visibility.enabled_categories(session, owner_id=owner.id)
    return AiVisibilityPage(
        items=[
            AiVisibilityOut(
                category=category,
                label=_CATEGORY_LABELS[category],
                enabled=category in enabled,
                has_ai_path=category in _CATEGORIES_WITH_AI_PATH,
            )
            for category in AiVisibilityCategory
        ],
        medications_note=(
            "Imported medication lists are reference history only. They are never "
            "shown to chat or AI and never become your plan or recorded doses."
        ),
    )


@router.put(
    "/ai-visibility/{category}",
    response_model=AiVisibilityOut,
    dependencies=[Depends(require_csrf)],
)
def set_ai_visibility(
    category: AiVisibilityCategory,
    payload: AiVisibilityIn,
    session: DbSession,
    owner: CurrentOwner,
) -> AiVisibilityOut:
    visibility.set_category(session, owner_id=owner.id, category=category, enabled=payload.enabled)
    session.commit()
    return AiVisibilityOut(
        category=category,
        label=_CATEGORY_LABELS[category],
        enabled=payload.enabled,
        has_ai_path=category in _CATEGORIES_WITH_AI_PATH,
    )


def _owned(session: DbSession, owner: CurrentOwner, record_id: uuid.UUID):
    row = (
        session.execute(
            select(HealthRecord, HealthRecordRevision)
            .join(HealthRecordRevision, _current_revision_join())
            .where(HealthRecord.owner_id == owner.id, HealthRecord.id == record_id)
        )
        .tuples()
        .first()
    )
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return row


@router.get("/{record_id}", response_model=HealthRecordDetailOut)
def get_health_record(
    record_id: uuid.UUID, session: DbSession, owner: CurrentOwner
) -> HealthRecordDetailOut:
    envelope, revision = _owned(session, owner, record_id)
    history = session.scalars(
        select(HealthRecordRevision)
        .where(
            HealthRecordRevision.owner_id == owner.id,
            HealthRecordRevision.health_record_id == envelope.id,
        )
        .order_by(HealthRecordRevision.revision.desc())
    )
    return HealthRecordDetailOut.model_validate(
        {
            **_out(envelope, revision),
            "body_text": revision.body_text,
            "file_paths": list(revision.file_paths or []),
            "revisions": [
                HealthRecordRevisionOut.model_validate(item, from_attributes=True)
                for item in history
            ],
        }
    )


def resolve_dataset_file(root: Path, relative: str) -> Path | None:
    """Resolve a dataset-relative path, refusing anything that escapes the root."""
    if not relative or relative.startswith(("/", "\\")) or "\x00" in relative:
        return None
    base = root.resolve(strict=False)
    candidate = (base / relative).resolve(strict=False)
    if not candidate.is_relative_to(base) or not candidate.is_file():
        return None
    if candidate.suffix.lower() not in _MEDIA_TYPES:
        return None
    return candidate


@router.get("/{record_id}/files/{index}", response_class=FileResponse)
def download_health_record_file(
    record_id: uuid.UUID,
    index: int,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> FileResponse:
    envelope, revision = _owned(session, owner, record_id)
    paths = list(revision.file_paths or [])
    root = settings.health_records_root
    if root is None or index < 0 or index >= len(paths):
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
    path = resolve_dataset_file(root, paths[index])
    if path is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="file not found")
    return FileResponse(
        path,
        media_type=_MEDIA_TYPES[path.suffix.lower()],
        filename=f"{envelope.dataset_table.value}-{envelope.record_id}{path.suffix.lower()}",
        content_disposition_type="attachment",
        headers={
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox; default-src 'none'",
        },
    )
