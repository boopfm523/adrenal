"""Manual laboratory entry and owner-confirmed CSV import."""

from __future__ import annotations

import copy
import hmac
import uuid
from datetime import UTC, date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, File, Form, HTTPException, Response, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import Field, model_validator
from sqlalchemy import func, select, text
from starlette.concurrency import run_in_threadpool

from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.ai.vision import apply_vision_fallback
from healthcurve.api.date_filters import local_date_window
from healthcurve.api.deps import (
    AppRateLimiter,
    AppSettings,
    CurrentOwner,
    DbSession,
    enforce_rate_limit,
    require_csrf,
)
from healthcurve.api.lab_deletion import (
    LabDeletionPreview,
    delete_lab_report_unit,
    preview_lab_report_deletion,
)
from healthcurve.api.pagination import Pagination, page_metadata
from healthcurve.api.routers.doses import resolve_time
from healthcurve.api.schemas import ApiModel, EventTimeIn, LabResultOut, LabResultPage, PageMetadata
from healthcurve.config import Settings
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.identity import service as auth
from healthcurve.labs.cleanup_jobs import enqueue_document_cleanup
from healthcurve.labs.documents import (
    DocumentLayout,
    DocumentStorageError,
    load_extraction_result,
    load_validation_result,
    mark_deleted,
    store_pdf_upload,
)
from healthcurve.labs.imports import MAX_CSV_BYTES, LabCandidate, LabImportError, parse_csv_import
from healthcurve.labs.models import LabDocument, LabDocumentStatus, LabPanel, LabResult
from healthcurve.labs.normalization import analyte_definition
from healthcurve.labs.pdf_schemas import PdfDraftCandidate
from healthcurve.labs.service import (
    LabConfirmationError,
    confirm_csv,
    create_panel,
    manual_candidate,
)
from healthcurve.operations import audit
from healthcurve.operations.jobs import JobQueueError
from healthcurve.operations.rate_limit import RateLimitPolicy

router = APIRouter(prefix="/labs", tags=["labs"])


@router.get("/results", response_model=LabResultPage)
def list_lab_results(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
) -> LabResultPage:
    """Return source facts and derived values together, never conflated."""
    query = (
        select(LabResult, LabPanel)
        .join(LabPanel, LabPanel.id == LabResult.panel_id)
        .where(LabResult.owner_id == owner.id, LabPanel.owner_id == owner.id)
    )
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    if window.start is not None:
        query = query.where(LabPanel.occurred_at >= window.start)
    if window.end_exclusive is not None:
        query = query.where(LabPanel.occurred_at < window.end_exclusive)
    total_items = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    metadata = page_metadata(total_items, pagination)
    rows = session.execute(
        query.order_by(LabPanel.occurred_at.desc(), LabResult.source_row_index, LabResult.id)
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()
    payload: list[LabResultOut] = []
    for result, panel in rows:
        definition = analyte_definition(result.normalized_analyte_code)
        payload.append(
            LabResultOut(
                id=result.id,
                panel_id=panel.id,
                source_document_id=result.source_document_id,
                source_page_number=result.source_page_number,
                analyte_name=result.analyte_name,
                original_value=result.original_value,
                qualitative_result=result.qualitative_result,
                original_unit=result.original_unit,
                original_reference_range=result.original_reference_range,
                abnormal_flag=result.abnormal_flag,
                normalized_analyte_code=result.normalized_analyte_code,
                normalized_analyte_name=(
                    definition.display_name if definition is not None else None
                ),
                normalized_value=result.normalized_value,
                normalized_unit=result.normalized_unit,
                normalization_method=result.normalization_method,
                specimen_time=panel.event_time,
                specimen_type=panel.specimen_type,
                laboratory_name=panel.laboratory_name,
                source_type=panel.source_type,
                confirmation_state=panel.confirmation_state,
            )
        )
    return LabResultPage(items=payload, page=metadata)


def _owned_document(
    session: DbSession,
    owner: CurrentOwner,
    document_id: uuid.UUID,
    *,
    for_update: bool = False,
) -> LabDocument:
    statement = select(LabDocument).where(
        LabDocument.id == document_id,
        LabDocument.owner_id == owner.id,
    )
    if for_update:
        statement = statement.with_for_update()
    document = session.scalar(statement)
    if document is None:
        raise HTTPException(status_code=404, detail={"code": "lab_document_not_found"})
    return document


def _reconcile_document(document: LabDocument, layout: DocumentLayout) -> None:
    if document.status is not LabDocumentStatus.PENDING:
        return
    try:
        result = load_validation_result(layout, document.id)
    except DocumentStorageError as exc:
        raise HTTPException(status_code=500, detail={"code": str(exc)}) from exc
    if result is None:
        return
    if not hmac.compare_digest(result.sha256, document.sha256):
        raise HTTPException(
            status_code=500, detail={"code": "document_validation_checksum_mismatch"}
        )
    document.validated_at = datetime.now(UTC)
    if result.status == "stored":
        if not layout.path("stored", document.id).is_file():
            raise HTTPException(status_code=500, detail={"code": "document_storage_missing"})
        document.status = LabDocumentStatus.STORED
        document.page_count = result.page_count
    else:
        document.status = LabDocumentStatus.REJECTED
        document.rejection_reason = result.reason_code or "pdf_validation_failed"


def _document_payload(document: LabDocument) -> dict[str, Any]:
    return {
        "document_id": str(document.id),
        "display_name": document.display_name,
        "media_type": document.media_type,
        "sha256": document.sha256,
        "byte_size": document.byte_size,
        "status": document.status.value,
        "page_count": document.page_count,
        "rejection_reason": document.rejection_reason,
        "created_at": document.created_at,
        "validated_at": document.validated_at,
    }


def _reconcile_extraction(
    session: DbSession,
    owner: CurrentOwner,
    document: LabDocument,
    layout: DocumentLayout,
    settings: Settings,
    response: Response,
    limiter: AppRateLimiter,
) -> ExtractionDraft | None:
    if document.status is not LabDocumentStatus.STORED:
        return None
    try:
        result = load_extraction_result(layout, document.id)
    except DocumentStorageError as exc:
        raise HTTPException(status_code=500, detail={"code": str(exc)}) from exc
    if result is None:
        return None
    if not hmac.compare_digest(result.sha256, document.sha256):
        raise HTTPException(status_code=500, detail={"code": "extraction_checksum_mismatch"})
    if session.get_bind().dialect.name == "postgresql":
        session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:key, 0))"),
            {"key": f"lab-extraction:{owner.id}:{document.id}"},
        )
    existing = session.scalar(
        select(ExtractionDraft).where(
            ExtractionDraft.owner_id == owner.id,
            ExtractionDraft.source == "lab_pdf",
            ExtractionDraft.provider_message_id == str(document.id),
        )
    )
    if existing is not None:
        return existing
    if result.vision_pages:
        enforce_rate_limit(
            response,
            limiter,
            scope="model",
            identity=str(owner.id),
            policy=RateLimitPolicy(settings.model_rate_limit, settings.model_rate_window_s),
            cost=len(result.vision_pages),
        )
        result = apply_vision_fallback(result, layout=layout, settings=settings)
    candidates = []
    for candidate in result.candidates:
        payload = candidate.model_dump(mode="json")
        payload.update(
            {
                "document_id": str(document.id),
                "document_sha256": document.sha256,
                "extractor_name": result.extractor_name,
                "extractor_version": result.extractor_version,
                "schema_version": result.schema_version,
                "adequate": result.adequate,
                "model_name": result.model_name,
                "model_digest": result.model_digest,
                "prompt_version": result.prompt_version,
            }
        )
        candidates.append(payload)
    draft = ExtractionDraft(
        owner_id=owner.id,
        source="lab_pdf",
        provider_message_id=str(document.id),
        raw_text=None,
        candidates=candidates,
        original_candidates=copy.deepcopy(candidates),
        state=DraftState.PENDING,
        model_name=result.model_name,
        model_digest=result.model_digest,
        prompt_version=result.prompt_version or "deterministic-no-prompt-v1",
        schema_version=result.schema_version,
    )
    session.add(draft)
    session.flush([draft])
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.RECORD_CREATED,
        target_type="lab_extraction_draft",
        target_id=draft.id,
        change_summary=(
            f"tier={result.extraction_tier};parsed={result.parsed_count};"
            f"unparsed={result.unparsed_count}"
        ),
    )
    return draft


@router.post(
    "/documents",
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
async def upload_lab_document(
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    file: Annotated[UploadFile, File()],
) -> dict[str, Any]:
    """Quarantine a bounded PDF for the no-network structural validation worker."""
    layout = DocumentLayout(settings.uploads_dir)
    try:
        upload = await run_in_threadpool(
            store_pdf_upload,
            file.file,
            layout=layout,
            submitted_name=file.filename,
            media_type=file.content_type,
        )
    except DocumentStorageError as exc:
        code = str(exc)
        response_status = 413 if code == "pdf_size_invalid" else 422
        if code == "pdf_media_type_invalid":
            response_status = 415
        raise HTTPException(status_code=response_status, detail={"code": code}) from exc
    finally:
        await file.close()
    document = LabDocument(
        id=upload.document_id,
        owner_id=owner.id,
        display_name=upload.display_name,
        media_type=upload.media_type,
        sha256=upload.sha256,
        byte_size=upload.byte_size,
        status=LabDocumentStatus.PENDING,
    )
    try:
        session.add(document)
        session.flush([document])
    except Exception:
        mark_deleted(layout, upload.document_id)
        raise
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.RECORD_CREATED,
        target_type="lab_document",
        target_id=document.id,
        change_summary="source=pdf;status=pending",
    )
    return _document_payload(document)


class LabDocumentPage(ApiModel):
    items: list[dict[str, Any]]
    page: PageMetadata


@router.get("/documents", response_model=LabDocumentPage)
def list_lab_documents(
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    pagination: Pagination,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
) -> LabDocumentPage:
    """List owner documents without starting model work for every historical file."""
    layout = DocumentLayout(settings.uploads_dir)
    query = select(LabDocument).where(
        LabDocument.owner_id == owner.id,
        LabDocument.status != LabDocumentStatus.DELETED,
    )
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    if window.start is not None:
        query = query.where(LabDocument.created_at >= window.start)
    if window.end_exclusive is not None:
        query = query.where(LabDocument.created_at < window.end_exclusive)
    total_items = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    metadata = page_metadata(total_items, pagination)
    documents = list(
        session.scalars(
            query.order_by(LabDocument.created_at.desc(), LabDocument.id)
            .offset(pagination.offset)
            .limit(pagination.page_size)
        )
    )
    document_ids = {str(document.id) for document in documents}
    drafts = {
        draft.provider_message_id: draft
        for draft in session.scalars(
            select(ExtractionDraft).where(
                ExtractionDraft.owner_id == owner.id,
                ExtractionDraft.source == "lab_pdf",
                ExtractionDraft.provider_message_id.in_(document_ids),
            )
        )
    }
    payload: list[dict[str, Any]] = []
    for document in documents:
        _reconcile_document(document, layout)
        item = _document_payload(document)
        draft = drafts.get(str(document.id))
        item["extraction_status"] = "draft_ready" if draft is not None else "pending"
        item["extraction_draft_id"] = str(draft.id) if draft is not None else None
        item["draft_state"] = draft.state.value if draft is not None else None
        payload.append(item)
    return LabDocumentPage(items=payload, page=metadata)


@router.get("/documents/{document_id}")
def get_lab_document(
    document_id: uuid.UUID,
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    limiter: AppRateLimiter,
) -> dict[str, Any]:
    document = _owned_document(session, owner, document_id)
    layout = DocumentLayout(settings.uploads_dir)
    _reconcile_document(document, layout)
    draft = _reconcile_extraction(session, owner, document, layout, settings, response, limiter)
    payload = _document_payload(document)
    payload["extraction_status"] = "draft_ready" if draft is not None else "pending"
    payload["extraction_draft_id"] = str(draft.id) if draft is not None else None
    return payload


@router.get("/documents/{document_id}/extraction")
def get_lab_document_extraction(
    document_id: uuid.UUID,
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    limiter: AppRateLimiter,
) -> dict[str, Any]:
    document = _owned_document(session, owner, document_id)
    layout = DocumentLayout(settings.uploads_dir)
    _reconcile_document(document, layout)
    draft = _reconcile_extraction(session, owner, document, layout, settings, response, limiter)
    if draft is None:
        raise HTTPException(status_code=409, detail={"code": "lab_extraction_not_ready"})
    return {
        "category": "ai_draft",
        "draft_id": str(draft.id),
        "document_id": str(document.id),
        "state": draft.state.value,
        "schema_version": draft.schema_version,
        "prompt_version": draft.prompt_version,
        "model_name": draft.model_name,
        "candidates": draft.candidates,
    }


@router.get("/documents/{document_id}/download", response_class=FileResponse)
def download_lab_document(
    document_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> FileResponse:
    document = _owned_document(session, owner, document_id)
    layout = DocumentLayout(settings.uploads_dir)
    _reconcile_document(document, layout)
    if document.status is not LabDocumentStatus.STORED:
        raise HTTPException(status_code=409, detail={"code": "lab_document_not_available"})
    path = layout.path("stored", document.id)
    if not path.is_file():
        raise HTTPException(status_code=500, detail={"code": "document_storage_missing"})
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=f"lab-document-{document.id}.pdf",
        content_disposition_type="attachment",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


@router.get("/documents/{document_id}/pages/{page_number}/preview", response_class=FileResponse)
def preview_lab_document_page(
    document_id: uuid.UUID,
    page_number: int,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> FileResponse:
    """Return a networkless-rendered inert PNG; raw PDFs remain attachment-only."""
    document = _owned_document(session, owner, document_id)
    layout = DocumentLayout(settings.uploads_dir)
    _reconcile_document(document, layout)
    if document.status is not LabDocumentStatus.STORED:
        raise HTTPException(status_code=409, detail={"code": "lab_document_not_available"})
    if document.page_count is None or not 1 <= page_number <= document.page_count:
        raise HTTPException(status_code=404, detail={"code": "lab_source_page_not_found"})
    path = layout.preview_path(document.id, page_number)
    if not path.is_file():
        raise HTTPException(status_code=409, detail={"code": "lab_source_preview_unavailable"})
    return FileResponse(
        path,
        media_type="image/png",
        filename=f"lab-document-{document.id}-page-{page_number}.png",
        content_disposition_type="inline",
        headers={
            "Cache-Control": "no-store",
            "Content-Security-Policy": "default-src 'none'; sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )


class PdfLabCandidateConfirmIn(ApiModel):
    candidate_index: int = Field(ge=0, le=1_999)
    included: bool = True
    analyte_name: str = Field(min_length=1, max_length=500)
    original_value: str = Field(min_length=1, max_length=300)
    original_unit: str | None = Field(default=None, max_length=120)
    original_reference_range: str | None = Field(default=None, max_length=300)


class PdfLabConfirmIn(ApiModel):
    specimen_time: EventTimeIn
    report_time: EventTimeIn
    laboratory_name: str | None = Field(default=None, max_length=300)
    accession_id: str | None = Field(default=None, max_length=255)
    specimen_type: str | None = Field(default=None, max_length=255)
    report_status: str | None = Field(default=None, max_length=120)
    candidates: list[PdfLabCandidateConfirmIn] = Field(min_length=1, max_length=2_000)

    @model_validator(mode="after")
    def candidates_are_unique_and_some_are_included(self) -> PdfLabConfirmIn:
        indexes = [candidate.candidate_index for candidate in self.candidates]
        if len(indexes) != len(set(indexes)):
            raise ValueError("candidate indexes must be unique")
        if not any(candidate.included for candidate in self.candidates):
            raise ValueError("at least one candidate must be included")
        return self


@router.post(
    "/documents/{document_id}/confirm",
    dependencies=[Depends(require_csrf)],
)
def confirm_lab_document(
    document_id: uuid.UUID,
    payload: PdfLabConfirmIn,
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
    limiter: AppRateLimiter,
):
    """Promote only owner-reviewed PDF candidates across the AI-to-fact boundary."""
    document = _owned_document(session, owner, document_id, for_update=True)
    if document.status is LabDocumentStatus.DELETED:
        raise HTTPException(status_code=409, detail={"code": "lab_document_deleted"})
    layout = DocumentLayout(settings.uploads_dir)
    _reconcile_document(document, layout)
    _reconcile_extraction(session, owner, document, layout, settings, response, limiter)
    draft = session.scalar(
        select(ExtractionDraft)
        .where(
            ExtractionDraft.owner_id == owner.id,
            ExtractionDraft.source == "lab_pdf",
            ExtractionDraft.provider_message_id == str(document.id),
        )
        .with_for_update()
    )
    if draft is None:
        raise HTTPException(status_code=409, detail={"code": "lab_extraction_not_ready"})
    if not draft.is_pending:
        if draft.created_event_ids:
            try:
                existing_id = uuid.UUID(draft.created_event_ids[0])
            except (ValueError, TypeError):
                existing_id = None
            existing = session.get(LabPanel, existing_id) if existing_id is not None else None
            if existing is not None and existing.owner_id == owner.id:
                return _panel_payload(existing, created=False)
        raise HTTPException(status_code=409, detail={"code": "lab_draft_already_resolved"})

    specimen = resolve_time(payload.specimen_time)
    report = resolve_time(payload.report_time)
    if report.occurred_at < specimen.occurred_at:
        raise HTTPException(status_code=422, detail={"code": "report_before_specimen"})

    candidates: list[LabCandidate] = []
    edited_payload = copy.deepcopy(draft.candidates)
    changed = False
    for requested in payload.candidates:
        if requested.candidate_index >= len(draft.candidates):
            raise HTTPException(status_code=422, detail={"code": "lab_candidate_not_found"})
        stored_payload = draft.candidates[requested.candidate_index]
        if stored_payload.get("document_id") != str(document.id):
            raise HTTPException(status_code=422, detail={"code": "lab_candidate_source_mismatch"})
        try:
            stored = PdfDraftCandidate.model_validate(stored_payload)
        except ValueError as exc:
            raise HTTPException(status_code=422, detail={"code": "lab_candidate_invalid"}) from exc
        if not stored.parsed:
            raise HTTPException(status_code=422, detail={"code": "lab_candidate_unparsed"})
        original = (
            stored.analyte_name,
            stored.original_value,
            stored.original_unit,
            stored.original_reference_range,
        )
        reviewed = (
            requested.analyte_name,
            requested.original_value,
            requested.original_unit,
            requested.original_reference_range,
        )
        changed = changed or not requested.included or original != reviewed
        edited_payload[requested.candidate_index].update(
            {
                "included": requested.included,
                "analyte_name": requested.analyte_name,
                "original_value": requested.original_value,
                "original_unit": requested.original_unit,
                "original_reference_range": requested.original_reference_range,
            }
        )
        if not requested.included:
            continue
        if not layout.preview_path(document.id, stored.page_number).is_file():
            raise HTTPException(
                status_code=409,
                detail={"code": "lab_source_preview_unavailable"},
            )
        candidates.append(
            LabCandidate(
                source_row_index=stored.row_index,
                source_page_number=stored.page_number,
                analyte_name=requested.analyte_name,
                original_value=requested.original_value,
                original_unit=requested.original_unit,
                original_reference_range=requested.original_reference_range,
            )
        )
    parsed_count = sum(
        1
        for candidate in draft.candidates
        if bool(candidate.get("parsed")) and candidate.get("document_id") == str(document.id)
    )
    if len(payload.candidates) != parsed_count:
        raise HTTPException(status_code=422, detail={"code": "lab_candidate_set_incomplete"})
    try:
        panel = create_panel(
            session,
            owner_id=owner.id,
            specimen_time=specimen,
            report_time=report,
            candidates=candidates,
            source_type=SourceType.FILE_IMPORT,
            confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
            provider_id=str(document.id),
            source_revision=document.sha256,
            laboratory_name=payload.laboratory_name,
            accession_id=payload.accession_id,
            specimen_type=payload.specimen_type,
            report_status=payload.report_status,
            source_document_id=document.id,
        )
    except LabConfirmationError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    draft.candidates = edited_payload
    draft.state = DraftState.EDITED if changed else DraftState.CONFIRMED
    draft.resolved_at = datetime.now(UTC)
    draft.created_event_ids = [str(panel.id)]
    draft.purge_raw_text()
    return _panel_payload(panel, created=True)


class LabDeletionPreviewOut(ApiModel):
    document_id: uuid.UUID
    mode: Literal["unconfirmed_upload", "confirmed_report"]
    requires_password: bool
    confirmation_phrase: str
    extraction_draft_ids: tuple[uuid.UUID, ...]
    panel_ids: tuple[uuid.UUID, ...]
    result_ids: tuple[uuid.UUID, ...]
    derived_result_count: int
    trend_point_count: int
    ai_analysis_ids: tuple[uuid.UUID, ...]
    report_snapshot_ids: tuple[uuid.UUID, ...]
    report_artifact_ids: tuple[uuid.UUID, ...]
    page_preview_count: int
    private_storage_artifact_count: int
    backups_may_retain_until_expiry: Literal[True] = True


class LabDeletionIn(ApiModel):
    password: str | None = Field(default=None, min_length=1, max_length=512)
    confirmation: str = Field(min_length=1, max_length=120)


class LabDeletionAcceptedOut(ApiModel):
    status: Literal["deletion_queued", "already_deleted"]
    document_id: uuid.UUID
    cleanup_task_count: int = Field(ge=1)


def _preview_out(preview: LabDeletionPreview) -> LabDeletionPreviewOut:
    return LabDeletionPreviewOut.model_validate(preview)


@router.get(
    "/documents/{document_id}/deletion-preview",
    response_model=LabDeletionPreviewOut,
)
def preview_lab_document_deletion(
    document_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> LabDeletionPreviewOut:
    document = _owned_document(session, owner, document_id)
    if document.status is LabDocumentStatus.DELETED:
        raise HTTPException(status_code=410, detail={"code": "lab_document_deleted"})
    try:
        preview = preview_lab_report_deletion(
            session,
            owner_id=owner.id,
            document=document,
            layout=DocumentLayout(settings.uploads_dir),
        )
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "lab_deletion_preview_storage_unavailable"},
        ) from exc
    return _preview_out(preview)


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=LabDeletionAcceptedOut,
    dependencies=[Depends(require_csrf)],
)
def delete_lab_document(
    document_id: uuid.UUID,
    payload: LabDeletionIn,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> LabDeletionAcceptedOut:
    document = _owned_document(session, owner, document_id, for_update=True)
    if document.status is LabDocumentStatus.DELETED:
        try:
            enqueue_document_cleanup(session, document.id)
        except JobQueueError as exc:
            raise HTTPException(
                status_code=503, detail={"code": "lab_deletion_queue_unavailable"}
            ) from exc
        return LabDeletionAcceptedOut(
            status="already_deleted", document_id=document.id, cleanup_task_count=1
        )
    try:
        preview = preview_lab_report_deletion(
            session,
            owner_id=owner.id,
            document=document,
            layout=DocumentLayout(settings.uploads_dir),
        )
    except OSError as exc:
        raise HTTPException(
            status_code=503,
            detail={"code": "lab_deletion_preview_storage_unavailable"},
        ) from exc
    if payload.confirmation != preview.confirmation_phrase:
        raise HTTPException(status_code=422, detail={"code": "lab_deletion_confirmation_mismatch"})
    if preview.requires_password and (
        payload.password is None or not auth.verify_password(owner.password_hash, payload.password)
    ):
        raise HTTPException(status_code=403, detail={"code": "current_password_incorrect"})
    try:
        delete_lab_report_unit(
            session,
            owner_id=owner.id,
            document=document,
            preview=preview,
        )
    except JobQueueError as exc:
        raise HTTPException(
            status_code=503, detail={"code": "lab_deletion_queue_unavailable"}
        ) from exc
    return LabDeletionAcceptedOut(
        status="deletion_queued",
        document_id=document.id,
        cleanup_task_count=preview.cleanup_task_count,
    )


class ManualLabResultIn(ApiModel):
    analyte_name: str = Field(min_length=1)
    original_value: str | None = None
    qualitative_result: str | None = None
    original_unit: str | None = None
    original_reference_range: str | None = None
    abnormal_flag: str | None = None
    normalized_analyte_code: str | None = None

    @model_validator(mode="after")
    def has_result(self) -> ManualLabResultIn:
        if self.original_value is None and self.qualitative_result is None:
            raise ValueError("original_value or qualitative_result is required")
        return self


class ManualLabPanelIn(ApiModel):
    specimen_time: EventTimeIn
    report_time: EventTimeIn
    laboratory_name: str | None = Field(default=None, max_length=300)
    accession_id: str | None = Field(default=None, max_length=255)
    specimen_type: str | None = Field(default=None, max_length=255)
    report_status: str | None = Field(default=None, max_length=120)
    results: list[ManualLabResultIn] = Field(min_length=1, max_length=1_000)


@router.post(
    "/manual",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_manual_lab(payload: ManualLabPanelIn, session: DbSession, owner: CurrentOwner):
    specimen = resolve_time(payload.specimen_time)
    report = resolve_time(payload.report_time)
    if report.occurred_at < specimen.occurred_at:
        raise HTTPException(status_code=422, detail={"code": "report_before_specimen"})
    candidates = [
        manual_candidate(source_row_index=index, **result.model_dump())
        for index, result in enumerate(payload.results)
    ]
    try:
        panel = create_panel(
            session,
            owner_id=owner.id,
            specimen_time=specimen,
            report_time=report,
            candidates=candidates,
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            laboratory_name=payload.laboratory_name,
            accession_id=payload.accession_id,
            specimen_type=payload.specimen_type,
            report_status=payload.report_status,
        )
    except LabConfirmationError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    return _panel_payload(panel, created=True)


async def _parse_csv(
    file: UploadFile,
    *,
    mapping_json: str,
    analyte_map_json: str,
    specimen_local: datetime,
    report_local: datetime,
    timezone: str,
):
    payload = await file.read(MAX_CSV_BYTES + 1)
    try:
        return parse_csv_import(
            source_name=file.filename,
            payload=payload,
            mapping_json=mapping_json,
            analyte_map_json=analyte_map_json,
            specimen_local=specimen_local,
            report_local=report_local,
            timezone=timezone,
        )
    except LabImportError as exc:
        code = str(exc)
        http_status = 413 if code == "csv_size_invalid" else 422
        raise HTTPException(status_code=http_status, detail={"code": code}) from exc
    finally:
        await file.close()


@router.post("/imports/csv/preview", dependencies=[Depends(require_csrf)])
async def preview_csv(
    owner: CurrentOwner,
    file: Annotated[UploadFile, File()],
    mapping_json: Annotated[str, Form()],
    specimen_local: Annotated[datetime, Form()],
    report_local: Annotated[datetime, Form()],
    timezone: Annotated[str | None, Form()] = None,
    analyte_map_json: Annotated[str, Form()] = "{}",
) -> dict[str, Any]:
    parsed = await _parse_csv(
        file,
        mapping_json=mapping_json,
        analyte_map_json=analyte_map_json,
        specimen_local=specimen_local,
        report_local=report_local,
        timezone=timezone or owner.default_timezone,
    )
    return {
        "creates_facts": False,
        "source_sha256": parsed.source_sha256,
        "mapping_sha256": parsed.mapping_sha256,
        "specimen_time": parsed.specimen_time,
        "report_time": parsed.report_time,
        "candidates": [candidate.model_dump() for candidate in parsed.candidates],
    }


@router.post("/imports/csv/confirm", dependencies=[Depends(require_csrf)])
async def confirm_csv_route(
    session: DbSession,
    owner: CurrentOwner,
    file: Annotated[UploadFile, File()],
    expected_sha256: Annotated[str, Form(min_length=64, max_length=64)],
    mapping_json: Annotated[str, Form()],
    specimen_local: Annotated[datetime, Form()],
    report_local: Annotated[datetime, Form()],
    timezone: Annotated[str | None, Form()] = None,
    analyte_map_json: Annotated[str, Form()] = "{}",
) -> dict[str, Any]:
    parsed = await _parse_csv(
        file,
        mapping_json=mapping_json,
        analyte_map_json=analyte_map_json,
        specimen_local=specimen_local,
        report_local=report_local,
        timezone=timezone or owner.default_timezone,
    )
    if not hmac.compare_digest(parsed.source_sha256, expected_sha256.casefold()):
        raise HTTPException(status_code=409, detail={"code": "preview_checksum_mismatch"})
    try:
        result = confirm_csv(session, owner_id=owner.id, parsed=parsed)
    except LabConfirmationError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    return _panel_payload(result.panel, created=result.created)


def _panel_payload(panel: Any, *, created: bool) -> dict[str, Any]:
    return {
        "category": "fact",
        "panel_id": str(panel.id),
        "created": created,
        "specimen": panel.event_time,
        "reported_at": panel.reported_at,
        "reported_local_time": panel.reported_local_time,
        "reported_timezone": panel.reported_timezone,
        "reported_utc_offset_minutes": panel.reported_utc_offset_minutes,
        "result_count": len(panel.results),
    }
