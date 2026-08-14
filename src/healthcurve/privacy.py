"""Physical deletion operations with audit retention and explicit dependency order."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import delete, false, func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthcurve.ai.models import AIAnalysis, ExtractionDraft, TelegramConversationContext
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
    GarminSleepStageInterval,
    GarminSyncRun,
    WearableDailySummary,
)
from healthcurve.integrations.telegram.models import TelegramLocationRequest, TelegramUpdate
from healthcurve.labs.documents import DocumentLayout, mark_deleted
from healthcurve.labs.models import LabDocument, LabPanel, LabResult
from healthcurve.medications.models import (
    DoseEvent,
    Medication,
    RegimenVersion,
)
from healthcurve.operations import audit
from healthcurve.operations.jobs import Job, JobStatus
from healthcurve.private_exports.models import PrivateExport
from healthcurve.reports.models import ReportSnapshot
from healthcurve.reports.storage import delete_owner_artifacts
from healthcurve.vitals.models import BloodPressureEvent, TemperatureEvent, WeightEvent


class DeletionError(RuntimeError):
    pass


class CorrectionHistoryError(DeletionError):
    pass


class RegimenDeletionError(DeletionError):
    """A development plan could not be removed without corrupting retained data."""


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
    "temperature": TemperatureEvent,
}


@dataclass(frozen=True, slots=True)
class IntegrationDeletion:
    credentials: int
    data_rows: int
    disconnect_requested: bool = False


@dataclass(frozen=True, slots=True)
class RegimenDeletion:
    slots: int
    instructions: int
    detached_doses: int
    hidden_analyses: int
    retained_reports: int


def _contains_reference(value: object, target: str) -> bool:
    if isinstance(value, dict):
        return any(_contains_reference(item, target) for item in value.values())
    if isinstance(value, (list, tuple, set)):
        return any(_contains_reference(item, target) for item in value)
    return value == target


def delete_development_regimen(
    session: Session,
    *,
    owner_id: uuid.UUID,
    version_id: uuid.UUID,
) -> RegimenDeletion | None:
    """Delete an owner-owned development plan without deleting recorded facts.

    Dose facts are detached from the deleted plan and slots so later analytics cannot
    compare them with a plan that no longer exists. Generated analyses citing the plan
    are hidden. Immutable reports remain frozen historical artifacts and may explicitly
    retain the deleted plan in their snapshot.
    """
    version = session.scalar(
        select(RegimenVersion).where(RegimenVersion.id == version_id).with_for_update()
    )
    if version is None or version.owner_id != owner_id:
        return None

    slot_ids = tuple(slot.id for slot in version.slots)
    version_key = str(version_id)
    report_references = sum(
        _contains_reference(snapshot.source_manifest, version_key)
        or _contains_reference(snapshot.snapshot_content, version_key)
        for snapshot in session.scalars(
            select(ReportSnapshot).where(ReportSnapshot.owner_id == owner_id)
        )
    )

    try:
        with session.begin_nested():
            dose_query = (
                select(DoseEvent)
                .where(
                    DoseEvent.owner_id == owner_id,
                    or_(
                        DoseEvent.regimen_version_id == version_id,
                        DoseEvent.slot_id.in_(slot_ids) if slot_ids else false(),
                    ),
                )
                .with_for_update(of=DoseEvent)
            )
            doses = tuple(session.scalars(dose_query))
            for dose in doses:
                if dose.regimen_version_id == version_id:
                    dose.regimen_version_id = None
                if dose.slot_id in slot_ids:
                    dose.slot_id = None

            analyses = tuple(
                session.scalars(
                    select(AIAnalysis)
                    .where(AIAnalysis.owner_id == owner_id, AIAnalysis.hidden_at.is_(None))
                    .with_for_update()
                )
            )
            invalid_analyses = tuple(
                analysis
                for analysis in analyses
                if _contains_reference(analysis.source_record_ids, version_key)
                or _contains_reference(analysis.computed_inputs, version_key)
            )
            invalidated_at = datetime.now(UTC)
            for analysis in invalid_analyses:
                analysis.hidden_at = invalidated_at

            result = RegimenDeletion(
                slots=len(version.slots),
                instructions=len(version.instructions),
                detached_doses=len(doses),
                hidden_analyses=len(invalid_analyses),
                retained_reports=report_references,
            )
            status_value = version.status.value
            session.delete(version)
            session.flush()
    except IntegrityError as exc:
        raise RegimenDeletionError(
            "plan has an unexpected retained reference and cannot be deleted"
        ) from exc
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.REGIMEN_DELETED,
        target_type="regimen_version",
        target_id=version_id,
        change_summary=(
            f"development plan deleted; status={status_value}; slots={result.slots}; "
            f"instructions={result.instructions}; detached_doses={result.detached_doses}; "
            f"hidden_analyses={result.hidden_analyses}; retained_reports={result.retained_reports}"
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
        data_rows += _delete_count(
            session,
            delete(WearableDailySummary).where(WearableDailySummary.owner_id == owner_id),
        )
        data_rows += (
            session.scalar(
                select(func.count())
                .select_from(GarminSleepStageInterval)
                .join(
                    GarminSleepEvent,
                    GarminSleepEvent.id == GarminSleepStageInterval.sleep_event_id,
                )
                .where(GarminSleepEvent.owner_id == owner_id)
            )
            or 0
        )
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
    if provider == "telegram":
        # Conversation context is short-lived integration working data, not a health
        # record. It has no purpose after disconnect and is removed even when the
        # owner elects to retain confirmed Telegram-origin facts.
        data_rows += _delete_count(
            session,
            delete(TelegramConversationContext).where(
                TelegramConversationContext.owner_id == owner_id
            ),
        )
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
    export_jobs = tuple(
        session.execute(
            select(PrivateExport.job_id, Job.status)
            .join(Job, Job.id == PrivateExport.job_id)
            .where(PrivateExport.owner_id == owner_id)
            .with_for_update(of=(PrivateExport, Job))
        )
    )
    if any(status in (JobStatus.QUEUED, JobStatus.RUNNING) for _, status in export_jobs):
        raise DeletionError("wait for the private export to finish before deleting the account")
    export_job_ids = tuple(job_id for job_id, _ in export_jobs)
    document_ids = list(
        session.scalars(select(LabDocument.id).where(LabDocument.owner_id == owner_id))
    )
    for document_id in document_ids:
        mark_deleted(DocumentLayout(uploads_dir), document_id)
    if report_artifacts_dir is not None:
        delete_owner_artifacts(report_artifacts_dir, owner_id)

    if export_job_ids:
        # Deleting jobs cascades their linked export request rows; job payloads cannot
        # outlive the owner identifier they reference.
        session.execute(delete(Job).where(Job.id.in_(export_job_ids)))

    # Children and records that restrict owner deletion first. Database cascades remove
    # lab results, regimen children, and Garmin facts from their owning parent rows.
    for model in (
        AIAnalysis,
        ExtractionDraft,
        TelegramConversationContext,
        LabPanel,
        LabDocument,
        ReportSnapshot,
    ):
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
        TemperatureEvent,
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
