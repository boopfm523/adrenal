"""Manual laboratory entry and owner-confirmed CSV import."""

from __future__ import annotations

import copy
import hmac
import uuid
from datetime import UTC, datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from fastapi.responses import FileResponse
from pydantic import Field, model_validator
from sqlalchemy import select, text
from starlette.concurrency import run_in_threadpool

from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.ai.vision import apply_vision_fallback
from healthcurve.api.deps import AppSettings, CurrentOwner, DbSession, require_csrf
from healthcurve.api.routers.doses import resolve_time
from healthcurve.api.schemas import ApiModel, EventTimeIn
from healthcurve.config import Settings
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.labs.documents import (
    DocumentLayout,
    DocumentStorageError,
    load_extraction_result,
    load_validation_result,
    mark_deleted,
    store_pdf_upload,
)
from healthcurve.labs.imports import MAX_CSV_BYTES, LabImportError, parse_csv_import
from healthcurve.labs.models import LabDocument, LabDocumentStatus
from healthcurve.labs.service import (
    LabConfirmationError,
    confirm_csv,
    create_panel,
    manual_candidate,
)
from healthcurve.operations import audit

router = APIRouter(prefix="/labs", tags=["labs"])


def _owned_document(session: DbSession, owner: CurrentOwner, document_id: uuid.UUID) -> LabDocument:
    document = session.scalar(
        select(LabDocument).where(
            LabDocument.id == document_id,
            LabDocument.owner_id == owner.id,
        )
    )
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
    result = apply_vision_fallback(result, layout=layout, settings=settings)
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


@router.get("/documents/{document_id}")
def get_lab_document(
    document_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> dict[str, Any]:
    document = _owned_document(session, owner, document_id)
    layout = DocumentLayout(settings.uploads_dir)
    _reconcile_document(document, layout)
    draft = _reconcile_extraction(session, owner, document, layout, settings)
    payload = _document_payload(document)
    payload["extraction_status"] = "draft_ready" if draft is not None else "pending"
    payload["extraction_draft_id"] = str(draft.id) if draft is not None else None
    return payload


@router.get("/documents/{document_id}/extraction")
def get_lab_document_extraction(
    document_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> dict[str, Any]:
    document = _owned_document(session, owner, document_id)
    layout = DocumentLayout(settings.uploads_dir)
    _reconcile_document(document, layout)
    draft = _reconcile_extraction(session, owner, document, layout, settings)
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


@router.delete(
    "/documents/{document_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_lab_document(
    document_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> None:
    document = _owned_document(session, owner, document_id)
    if document.status is LabDocumentStatus.DELETED:
        return
    draft = session.scalar(
        select(ExtractionDraft).where(
            ExtractionDraft.owner_id == owner.id,
            ExtractionDraft.source == "lab_pdf",
            ExtractionDraft.provider_message_id == str(document.id),
        )
    )
    try:
        mark_deleted(DocumentLayout(settings.uploads_dir), document.id)
    except OSError as exc:
        raise HTTPException(status_code=500, detail={"code": "document_deletion_failed"}) from exc
    document.status = LabDocumentStatus.DELETED
    document.deleted_at = datetime.now(UTC)
    # Keep only an opaque tombstone row for audit/race safety. The submitted name,
    # checksum, and size are source-document metadata and are scrubbed on deletion.
    document.display_name = "deleted.pdf"
    document.sha256 = "0" * 64
    document.byte_size = 1
    document.page_count = None
    document.rejection_reason = None
    if draft is not None:
        session.delete(draft)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.RECORD_DELETED,
        target_type="lab_document",
        target_id=document.id,
        change_summary="source_and_derivatives_deleted",
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
    candidates = [manual_candidate(**result.model_dump()) for result in payload.results]
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
