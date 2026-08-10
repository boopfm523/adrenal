"""Physical deletion operations with audit retention and explicit dependency order."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthcurve.ai.models import AIAnalysis, ExtractionDraft
from healthcurve.context.models import ContextEvent, SavedCoarseLocation
from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events.base import EventMixin
from healthcurve.events.models import DiaryEvent, LifeEvent, SymptomEvent
from healthcurve.identity.models import AuthSession, Owner
from healthcurve.integrations.credentials import IntegrationCredential
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminConnection,
    GarminConnectionState,
    GarminImportBatch,
    GarminMetricEvent,
    GarminSleepEvent,
    GarminSyncRun,
)
from healthcurve.integrations.telegram.models import TelegramLocationRequest, TelegramUpdate
from healthcurve.labs.documents import DocumentLayout, mark_deleted
from healthcurve.labs.models import LabDocument, LabPanel, LabResult
from healthcurve.medications.models import (
    DoseEvent,
    Medication,
    RegimenDoseSlot,
    RegimenStatus,
    RegimenVersion,
)
from healthcurve.operations import audit
from healthcurve.reports.models import ReportSnapshot
from healthcurve.reports.storage import delete_owner_artifacts
from healthcurve.vitals.models import BloodPressureEvent, WeightEvent


class DeletionError(RuntimeError):
    pass


class CorrectionHistoryError(DeletionError):
    pass


class RegimenDraftDeletionError(DeletionError):
    """A plan draft cannot be physically removed without losing provenance."""


DELETABLE_RECORDS: dict[str, type] = {
    "dose": DoseEvent,
    "symptom": SymptomEvent,
    "diary": DiaryEvent,
    "life_event": LifeEvent,
    "stress_episode": StressEpisode,
    "emergency_injection": EmergencyInjectionEvent,
    "lab_panel": LabPanel,
    "lab_document": LabDocument,
    "blood_pressure": BloodPressureEvent,
    "weight": WeightEvent,
}


@dataclass(frozen=True, slots=True)
class IntegrationDeletion:
    credentials: int
    data_rows: int
    disconnect_requested: bool = False


@dataclass(frozen=True, slots=True)
class RegimenDraftDeletion:
    slots: int
    instructions: int


def delete_regimen_draft(
    session: Session,
    *,
    owner_id: uuid.UUID,
    version_id: uuid.UUID,
) -> RegimenDraftDeletion | None:
    """Delete only an unapproved, owner-owned, completely unreferenced draft.

    Draft-owned slots and instructions are part of the same plan draft and may cascade.
    Facts, immutable report snapshots, and AI source manifests are independent records;
    any reference from them refuses the deletion instead of silently orphaning history.
    The caller owns the transaction, so an unexpected database reference rolls back the
    draft deletion and its audit entry together.
    """
    version = session.get(RegimenVersion, version_id)
    if version is None or version.owner_id != owner_id:
        return None
    if version.status is not RegimenStatus.DRAFT:
        raise RegimenDraftDeletionError(
            "approved and retired plan history cannot be deleted; retire an approved plan instead"
        )

    dose_references = (
        session.scalar(
            select(func.count())
            .select_from(DoseEvent)
            .where(
                DoseEvent.owner_id == owner_id,
                or_(
                    DoseEvent.regimen_version_id == version_id,
                    DoseEvent.slot_id.in_(
                        select(RegimenDoseSlot.id).where(
                            RegimenDoseSlot.regimen_version_id == version_id
                        )
                    ),
                ),
            )
        )
        or 0
    )
    version_key = str(version_id)
    report_references = sum(
        version_key in (manifest or {}).get("plan", [])
        for manifest in session.scalars(
            select(ReportSnapshot.source_manifest).where(ReportSnapshot.owner_id == owner_id)
        )
    )
    ai_references = sum(
        version_key in (source_ids or [])
        for source_ids in session.scalars(
            select(AIAnalysis.source_record_ids).where(AIAnalysis.owner_id == owner_id)
        )
    )
    if dose_references or report_references or ai_references:
        kinds = [
            label
            for count, label in (
                (dose_references, "recorded doses"),
                (report_references, "saved reports"),
                (ai_references, "AI analyses"),
            )
            if count
        ]
        raise RegimenDraftDeletionError(
            f"draft is referenced by {', '.join(kinds)} and cannot be deleted"
        )

    result = RegimenDraftDeletion(slots=len(version.slots), instructions=len(version.instructions))
    session.delete(version)
    try:
        session.flush()
    except IntegrityError as exc:
        raise RegimenDraftDeletionError(
            "draft has an unexpected retained reference and cannot be deleted"
        ) from exc
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.REGIMEN_DRAFT_DELETED,
        target_type="regimen_version",
        target_id=version_id,
        change_summary=(
            f"unapproved draft physically deleted; slots={result.slots}; "
            f"instructions={result.instructions}"
        ),
    )
    return result


def delete_record(
    session: Session,
    *,
    owner_id: uuid.UUID,
    record_type: str,
    record_id: uuid.UUID,
    uploads_dir: Path,
) -> bool:
    model = DELETABLE_RECORDS.get(record_type)
    if model is None:
        raise DeletionError("unsupported record type")
    row = session.get(model, record_id)
    if row is None or row.owner_id != owner_id:
        return False
    if issubclass(model, EventMixin):
        linked = row.supersedes_id is not None or session.scalar(
            select(model.id).where(model.supersedes_id == row.id)
        )
        if linked:
            raise CorrectionHistoryError(
                "correction-linked records cannot be deleted individually; delete the account"
            )
    if isinstance(row, LabDocument):
        linked_result = session.scalar(
            select(LabResult.id).where(
                LabResult.owner_id == owner_id,
                LabResult.source_document_id == row.id,
            )
        )
        if linked_result is not None:
            raise CorrectionHistoryError(
                "source-linked lab documents cannot be deleted individually; "
                "delete the lab panel or account"
            )
        mark_deleted(DocumentLayout(uploads_dir), row.id)
    session.delete(row)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.RECORD_DELETED,
        target_type=record_type,
        target_id=record_id,
        change_summary="physical deletion",
    )
    return True


def delete_integration(
    session: Session,
    *,
    owner_id: uuid.UUID,
    provider: str,
    delete_data: bool,
    telegram_chat_id: int | None,
) -> IntegrationDeletion:
    if provider not in {"garmin", "telegram", "weather"}:
        raise DeletionError("unsupported integration")
    credentials = _delete_count(
        session,
        delete(IntegrationCredential).where(
            IntegrationCredential.owner_id == owner_id,
            IntegrationCredential.provider == provider,
        ),
    )
    data_rows = 0
    disconnect_requested = False
    if delete_data and provider == "garmin":
        for model in (GarminMetricEvent, GarminSleepEvent, GarminActivityEvent):
            data_rows += _delete_event_chains(session, model, owner_id)
        data_rows += _delete_count(
            session, delete(GarminImportBatch).where(GarminImportBatch.owner_id == owner_id)
        )
        data_rows += _delete_count(
            session, delete(GarminSyncRun).where(GarminSyncRun.owner_id == owner_id)
        )
    if provider == "garmin":
        connection = session.scalar(
            select(GarminConnection).where(GarminConnection.owner_id == owner_id).with_for_update()
        )
        if connection is not None and connection.state is not GarminConnectionState.DISCONNECTED:
            connection.state = GarminConnectionState.DISCONNECT_PENDING
            disconnect_requested = True
    if delete_data and provider == "telegram":
        data_rows += _delete_count(
            session,
            delete(ContextEvent).where(
                ContextEvent.owner_id == owner_id,
                ContextEvent.provider_id.like("telegram-location:%"),
            ),
        )
        data_rows += _delete_count(
            session,
            delete(TelegramLocationRequest).where(TelegramLocationRequest.owner_id == owner_id),
        )
        data_rows += _delete_count(
            session,
            delete(SavedCoarseLocation).where(SavedCoarseLocation.owner_id == owner_id),
        )
        data_rows += _delete_count(
            session,
            delete(ExtractionDraft).where(
                ExtractionDraft.owner_id == owner_id, ExtractionDraft.source == "telegram"
            ),
        )
        if telegram_chat_id is not None:
            data_rows += _delete_count(
                session, delete(TelegramUpdate).where(TelegramUpdate.chat_id == telegram_chat_id)
            )
    if delete_data and provider == "weather":
        data_rows += _delete_count(
            session,
            delete(ContextEvent).where(
                ContextEvent.owner_id == owner_id,
                ContextEvent.weather_provider == "open-meteo",
            ),
        )
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.INTEGRATION_DISCONNECTED,
        target_type="integration",
        change_summary=f"provider={provider};data_deleted={delete_data}",
    )
    return IntegrationDeletion(credentials, data_rows, disconnect_requested)


def delete_account(
    session: Session,
    *,
    owner: Owner,
    uploads_dir: Path,
    telegram_chat_id: int | None,
    report_artifacts_dir: Path | None = None,
) -> None:
    owner_id = owner.id
    garmin_connection = session.scalar(
        select(GarminConnection).where(GarminConnection.owner_id == owner_id)
    )
    if (
        garmin_connection is not None
        and garmin_connection.state is not GarminConnectionState.DISCONNECTED
    ):
        raise DeletionError(
            "disconnect Garmin and wait for local token removal before deleting the account"
        )
    document_ids = list(
        session.scalars(select(LabDocument.id).where(LabDocument.owner_id == owner_id))
    )
    for document_id in document_ids:
        mark_deleted(DocumentLayout(uploads_dir), document_id)
    if report_artifacts_dir is not None:
        delete_owner_artifacts(report_artifacts_dir, owner_id)

    # Children and records that restrict owner deletion first. Database cascades remove
    # lab results, regimen children, and Garmin facts from their owning parent rows.
    for model in (AIAnalysis, ExtractionDraft, LabPanel, LabDocument, ReportSnapshot):
        session.execute(delete(model).where(model.owner_id == owner_id))
    for model in (GarminMetricEvent, GarminSleepEvent, GarminActivityEvent):
        _delete_event_chains(session, model, owner_id)
    session.execute(delete(GarminImportBatch).where(GarminImportBatch.owner_id == owner_id))
    session.execute(delete(GarminSyncRun).where(GarminSyncRun.owner_id == owner_id))
    session.execute(delete(GarminConnection).where(GarminConnection.owner_id == owner_id))
    for model in (
        EmergencyInjectionEvent,
        ContextEvent,
        DoseEvent,
        SymptomEvent,
        DiaryEvent,
        LifeEvent,
        BloodPressureEvent,
        WeightEvent,
    ):
        session.execute(delete(model).where(model.owner_id == owner_id))
    session.execute(delete(StressEpisode).where(StressEpisode.owner_id == owner_id))
    session.execute(delete(RegimenVersion).where(RegimenVersion.owner_id == owner_id))
    session.execute(delete(Medication).where(Medication.owner_id == owner_id))
    session.execute(delete(IntegrationCredential).where(IntegrationCredential.owner_id == owner_id))
    session.execute(delete(AuthSession).where(AuthSession.owner_id == owner_id))
    if telegram_chat_id is not None:
        session.execute(delete(TelegramUpdate).where(TelegramUpdate.chat_id == telegram_chat_id))

    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.DATA_DELETED,
        target_type="account",
        target_id=owner_id,
        change_summary=(
            "account health, plan, AI, integration, and identity data physically deleted"
        ),
    )
    session.delete(owner)


def _delete_count(session: Session, statement: object) -> int:
    result = session.execute(statement)  # type: ignore[arg-type]
    return max(result.rowcount or 0, 0)  # type: ignore[attr-defined]


def _delete_event_chains(session: Session, model: type[EventMixin], owner_id: uuid.UUID) -> int:
    """Delete complete owner-scoped correction chains from newest to oldest."""
    rows = list(session.scalars(select(model).where(model.owner_id == owner_id)))
    remaining = {row.id: row for row in rows}
    while remaining:
        superseded_ids = {
            row.supersedes_id for row in remaining.values() if row.supersedes_id is not None
        }
        leaves = [row for row in remaining.values() if row.id not in superseded_ids]
        if not leaves:
            raise DeletionError("correction history is cyclic and cannot be safely deleted")
        for row in leaves:
            session.delete(row)
            remaining.pop(row.id)
        session.flush()
    return len(rows)
