"""Physical deletion operations with audit retention and explicit dependency order."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from healthcurve.ai.models import AIAnalysis, ExtractionDraft
from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events.base import EventMixin
from healthcurve.events.models import DiaryEvent, LifeEvent, SymptomEvent
from healthcurve.identity.models import AuthSession, Owner
from healthcurve.integrations.credentials import IntegrationCredential
from healthcurve.integrations.garmin.models import GarminImportBatch
from healthcurve.integrations.telegram.models import TelegramUpdate
from healthcurve.labs.documents import DocumentLayout, mark_deleted
from healthcurve.labs.models import LabDocument, LabPanel
from healthcurve.medications.models import DoseEvent, Medication, RegimenVersion
from healthcurve.operations import audit
from healthcurve.reports.models import ReportSnapshot
from healthcurve.reports.storage import delete_owner_artifacts


class DeletionError(RuntimeError):
    pass


class CorrectionHistoryError(DeletionError):
    pass


DELETABLE_RECORDS: dict[str, type] = {
    "dose": DoseEvent,
    "symptom": SymptomEvent,
    "diary": DiaryEvent,
    "life_event": LifeEvent,
    "stress_episode": StressEpisode,
    "emergency_injection": EmergencyInjectionEvent,
    "lab_panel": LabPanel,
    "lab_document": LabDocument,
}


@dataclass(frozen=True, slots=True)
class IntegrationDeletion:
    credentials: int
    data_rows: int


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
    if provider not in {"garmin", "telegram"}:
        raise DeletionError("unsupported integration")
    credentials = _delete_count(
        session,
        delete(IntegrationCredential).where(
            IntegrationCredential.owner_id == owner_id,
            IntegrationCredential.provider == provider,
        ),
    )
    data_rows = 0
    if delete_data and provider == "garmin":
        data_rows = _delete_count(
            session, delete(GarminImportBatch).where(GarminImportBatch.owner_id == owner_id)
        )
    if delete_data and provider == "telegram":
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
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.INTEGRATION_DISCONNECTED,
        target_type="integration",
        change_summary=f"provider={provider};data_deleted={delete_data}",
    )
    return IntegrationDeletion(credentials, data_rows)


def delete_account(
    session: Session,
    *,
    owner: Owner,
    uploads_dir: Path,
    telegram_chat_id: int | None,
    report_artifacts_dir: Path | None = None,
) -> None:
    owner_id = owner.id
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
    session.execute(delete(GarminImportBatch).where(GarminImportBatch.owner_id == owner_id))
    for model in (EmergencyInjectionEvent, DoseEvent, SymptomEvent, DiaryEvent, LifeEvent):
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
