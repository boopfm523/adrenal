"""Cross-module orchestration for dependency-aware lab-report privacy deletion."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.ai.models import AIAnalysis, DraftState, ExtractionDraft
from healthcurve.labs.cleanup_jobs import enqueue_document_cleanup
from healthcurve.labs.documents import DocumentLayout
from healthcurve.labs.models import LabDocument, LabDocumentStatus, LabPanel, LabResult
from healthcurve.operations import audit
from healthcurve.reports.cleanup_jobs import enqueue_snapshot_artifact_cleanup
from healthcurve.reports.models import ReportArtifact, ReportSnapshot


@dataclass(frozen=True, slots=True)
class LabDeletionPreview:
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

    @property
    def cleanup_task_count(self) -> int:
        return 1 + len(self.report_snapshot_ids)


def _uuid_values(values: object) -> set[uuid.UUID]:
    if not isinstance(values, list):
        return set()
    identifiers: set[uuid.UUID] = set()
    for value in values:
        try:
            identifiers.add(uuid.UUID(str(value)))
        except (ValueError, TypeError, AttributeError):
            continue
    return identifiers


def _document_storage_counts(layout: DocumentLayout, document_id: uuid.UUID) -> tuple[int, int]:
    previews = list(layout.previews.glob(f"{document_id}-*.png"))
    fixed = [
        layout.path("quarantine", document_id),
        layout.path("quarantine", document_id, ".part"),
        layout.path("work", document_id),
        layout.path("stored", document_id),
        layout.path("results", document_id, ".json"),
        layout.path("extractions", document_id, ".json"),
    ]
    partials = [
        *layout.results.glob(f".{document_id}.*.part"),
        *layout.extractions.glob(f".{document_id}.*.part"),
        *layout.previews.glob(f".{document_id}-*.part"),
    ]
    storage_count = sum(path.is_file() for path in [*fixed, *previews, *partials])
    return len([path for path in previews if path.is_file()]), storage_count


def _matches_source_ids(values: object, source_ids: set[uuid.UUID]) -> bool:
    return bool(_uuid_values(values) & source_ids)


def preview_lab_report_deletion(
    session: Session,
    *,
    owner_id: uuid.UUID,
    document: LabDocument,
    layout: DocumentLayout,
) -> LabDeletionPreview:
    """Resolve every retained dependency without exposing lab content."""
    drafts = list(
        session.scalars(
            select(ExtractionDraft).where(
                ExtractionDraft.owner_id == owner_id,
                ExtractionDraft.source == "lab_pdf",
                ExtractionDraft.provider_message_id == str(document.id),
            )
        )
    )
    provider_panel_ids = set(
        session.scalars(
            select(LabPanel.id).where(
                LabPanel.owner_id == owner_id,
                LabPanel.provider_id == str(document.id),
            )
        )
    )
    for draft in drafts:
        provider_panel_ids.update(_uuid_values(draft.created_event_ids))

    results = list(
        session.scalars(
            select(LabResult).where(
                LabResult.owner_id == owner_id,
                LabResult.source_document_id == document.id,
            )
        )
    )
    panel_ids = provider_panel_ids | {result.panel_id for result in results}
    panels = (
        list(
            session.scalars(
                select(LabPanel).where(
                    LabPanel.owner_id == owner_id,
                    LabPanel.id.in_(panel_ids),
                )
            )
        )
        if panel_ids
        else []
    )
    # Include every result belonging to a linked panel, even if a future importer did
    # not attach the document ID to each row consistently.
    all_results = (
        list(
            session.scalars(
                select(LabResult).where(
                    LabResult.owner_id == owner_id,
                    LabResult.panel_id.in_([panel.id for panel in panels]),
                )
            )
        )
        if panels
        else results
    )
    results_by_id = {result.id: result for result in [*results, *all_results]}
    source_ids = {
        document.id,
        *(panel.id for panel in panels),
        *results_by_id,
    }

    analyses = [
        row
        for row in session.scalars(select(AIAnalysis).where(AIAnalysis.owner_id == owner_id))
        if _matches_source_ids(row.source_record_ids, source_ids)
    ]
    snapshots = [
        row
        for row in session.scalars(
            select(ReportSnapshot).where(ReportSnapshot.owner_id == owner_id)
        )
        if _matches_source_ids(row.source_manifest.get("fact"), source_ids)
    ]
    snapshot_ids = [snapshot.id for snapshot in snapshots]
    artifacts = (
        list(
            session.scalars(
                select(ReportArtifact).where(
                    ReportArtifact.owner_id == owner_id,
                    ReportArtifact.snapshot_id.in_(snapshot_ids),
                )
            )
        )
        if snapshot_ids
        else []
    )
    page_preview_count, private_storage_count = _document_storage_counts(layout, document.id)
    confirmed = bool(
        panels
        or results_by_id
        or any(draft.state in {DraftState.CONFIRMED, DraftState.EDITED} for draft in drafts)
    )
    suffix = str(document.id).replace("-", "")[-8:].upper()
    mode: Literal["unconfirmed_upload", "confirmed_report"] = (
        "confirmed_report" if confirmed else "unconfirmed_upload"
    )
    phrase = f"DELETE CONFIRMED LAB REPORT {suffix}" if confirmed else f"DELETE LAB UPLOAD {suffix}"
    ordered_results = sorted(results_by_id.values(), key=lambda row: str(row.id))
    return LabDeletionPreview(
        document_id=document.id,
        mode=mode,
        requires_password=confirmed,
        confirmation_phrase=phrase,
        extraction_draft_ids=tuple(sorted((row.id for row in drafts), key=str)),
        panel_ids=tuple(sorted((row.id for row in panels), key=str)),
        result_ids=tuple(row.id for row in ordered_results),
        derived_result_count=sum(
            result.normalized_value is not None or result.normalized_analyte_code is not None
            for result in ordered_results
        ),
        trend_point_count=sum(result.normalized_value is not None for result in ordered_results),
        ai_analysis_ids=tuple(sorted((row.id for row in analyses), key=str)),
        report_snapshot_ids=tuple(sorted(snapshot_ids, key=str)),
        report_artifact_ids=tuple(sorted((row.id for row in artifacts), key=str)),
        page_preview_count=page_preview_count,
        private_storage_artifact_count=private_storage_count,
    )


def delete_lab_report_unit(
    session: Session,
    *,
    owner_id: uuid.UUID,
    document: LabDocument,
    preview: LabDeletionPreview,
) -> None:
    """Delete the database unit and enqueue physical cleanup in one transaction."""
    for snapshot_id in preview.report_snapshot_ids:
        snapshot = session.scalar(
            select(ReportSnapshot).where(
                ReportSnapshot.id == snapshot_id,
                ReportSnapshot.owner_id == owner_id,
            )
        )
        if snapshot is None:
            continue
        enqueue_snapshot_artifact_cleanup(session, owner_id=owner_id, snapshot_id=snapshot.id)
        session.delete(snapshot)

    for analysis_id in preview.ai_analysis_ids:
        analysis = session.scalar(
            select(AIAnalysis).where(
                AIAnalysis.id == analysis_id,
                AIAnalysis.owner_id == owner_id,
            )
        )
        if analysis is not None:
            session.delete(analysis)
    for draft_id in preview.extraction_draft_ids:
        draft = session.scalar(
            select(ExtractionDraft).where(
                ExtractionDraft.id == draft_id,
                ExtractionDraft.owner_id == owner_id,
            )
        )
        if draft is not None:
            session.delete(draft)
    for panel_id in preview.panel_ids:
        panel = session.scalar(
            select(LabPanel).where(LabPanel.id == panel_id, LabPanel.owner_id == owner_id)
        )
        if panel is not None:
            session.delete(panel)

    document.status = LabDocumentStatus.DELETED
    document.deleted_at = datetime.now(UTC)
    document.display_name = "deleted.pdf"
    document.sha256 = "0" * 64
    document.byte_size = 1
    document.page_count = None
    document.rejection_reason = None
    enqueue_document_cleanup(session, document.id)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.RECORD_DELETED,
        target_type="lab_report_unit",
        target_id=document.id,
        change_summary=(
            f"mode={preview.mode};drafts={len(preview.extraction_draft_ids)};"
            f"panels={len(preview.panel_ids)};results={len(preview.result_ids)};"
            f"analyses={len(preview.ai_analysis_ids)};"
            f"reports={len(preview.report_snapshot_ids)};"
            f"cleanup_jobs={preview.cleanup_task_count}"
        ),
    )
    session.flush()
