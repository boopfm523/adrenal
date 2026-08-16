"""Repeatable-read, incremental JSON generation for complete owner exports."""

from __future__ import annotations

import base64
import hashlib
import json
import uuid
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from enum import Enum
from pathlib import Path
from typing import Any

from sqlalchemy import Select, func, select, text, update
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.orm import Session, sessionmaker

from healthcurve.ai.models import AIAnalysis, ExtractionDraft
from healthcurve.chat.models import ChatConversation, ChatMessage, ChatToolExecution
from healthcurve.context.models import ContextEvent, SavedCoarseLocation
from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events.models import DiaryEvent, LifeEvent, SymptomEvent
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminConnection,
    GarminImportBatch,
    GarminMetricEvent,
    GarminSleepEvent,
    GarminSleepStageInterval,
    GarminSyncRun,
    WearableDailySummary,
)
from healthcurve.labs.documents import DocumentLayout
from healthcurve.labs.models import LabDocument, LabDocumentStatus, LabPanel, LabResult
from healthcurve.medications.models import (
    ApprovedInstruction,
    DoseEvent,
    Medication,
    RegimenDoseSlot,
    RegimenVersion,
)
from healthcurve.operations import audit
from healthcurve.operations.jobs import JobQueueError
from healthcurve.private_exports.models import PrivateExport
from healthcurve.private_exports.storage import AtomicExportWriter, StoredExport
from healthcurve.reports.models import ReportArtifact, ReportSnapshot
from healthcurve.vitals.models import BloodPressureEvent, TemperatureEvent, WeightEvent

_BATCH_SIZE = 500
_PROGRESS_INTERVAL = 10_000


@dataclass(frozen=True, slots=True)
class ExportCollection:
    name: str
    statement: Select[Any]


def _direct(model: type[Any], owner_id: uuid.UUID) -> Select[Any]:
    table = model.__table__
    return select(table).where(table.c.owner_id == owner_id).order_by(*table.primary_key.columns)


def _child(
    model: type[Any], parent: type[Any], parent_key: str, owner_id: uuid.UUID
) -> Select[Any]:
    table = model.__table__
    parent_table = parent.__table__
    return (
        select(table)
        .join(parent_table, table.c[parent_key] == parent_table.c.id)
        .where(parent_table.c.owner_id == owner_id)
        .order_by(*table.primary_key.columns)
    )


def _collections(
    owner_id: uuid.UUID, *, include_ai: bool, include_sensitive: bool
) -> dict[str, list[ExportCollection]]:
    diary = _direct(DiaryEvent, owner_id)
    life = _direct(LifeEvent, owner_id)
    if not include_sensitive:
        diary = diary.where(DiaryEvent.__table__.c.is_sensitive.is_(False))
        life = life.where(LifeEvent.__table__.c.is_sensitive.is_(False))
    report_snapshots = _direct(ReportSnapshot, owner_id)
    report_artifacts = _direct(ReportArtifact, owner_id)
    if not include_ai:
        report_snapshots = report_snapshots.where(ReportSnapshot.__table__.c.include_ai.is_(False))
        report_artifacts = report_artifacts.where(
            ReportArtifact.__table__.c.snapshot_id.in_(
                select(ReportSnapshot.id).where(
                    ReportSnapshot.owner_id == owner_id,
                    ReportSnapshot.include_ai.is_(False),
                )
            )
        )
    if not include_sensitive:
        # Frozen reports do not retain a reliable "contains sensitive text" flag.
        # Excluding all is the only honest privacy-preserving behavior.
        report_snapshots = report_snapshots.where(text("false"))
        report_artifacts = report_artifacts.where(text("false"))

    saved_locations: list[ExportCollection] = []
    if include_sensitive:
        saved_locations.append(
            ExportCollection("saved_coarse_locations", _direct(SavedCoarseLocation, owner_id))
        )

    ai_rows: list[ExportCollection] = []
    if include_ai:
        ai_rows.append(ExportCollection("analyses", _direct(AIAnalysis, owner_id)))
        if include_sensitive:
            ai_rows.append(
                ExportCollection("extraction_drafts", _direct(ExtractionDraft, owner_id))
            )
            ai_rows.extend(
                (
                    ExportCollection("chat_conversations", _direct(ChatConversation, owner_id)),
                    ExportCollection("chat_messages", _direct(ChatMessage, owner_id)),
                    ExportCollection("chat_tool_executions", _direct(ChatToolExecution, owner_id)),
                )
            )

    return {
        "plan": [
            ExportCollection("medications", _direct(Medication, owner_id)),
            ExportCollection("regimen_versions", _direct(RegimenVersion, owner_id)),
            ExportCollection(
                "regimen_dose_slots",
                _child(RegimenDoseSlot, RegimenVersion, "regimen_version_id", owner_id),
            ),
            ExportCollection(
                "approved_instructions",
                _child(ApprovedInstruction, RegimenVersion, "regimen_version_id", owner_id),
            ),
        ],
        "facts": [
            ExportCollection("doses", _direct(DoseEvent, owner_id)),
            ExportCollection("symptoms", _direct(SymptomEvent, owner_id)),
            ExportCollection("diary_events", diary),
            ExportCollection("life_events", life),
            ExportCollection("stress_episodes", _direct(StressEpisode, owner_id)),
            ExportCollection("emergency_injections", _direct(EmergencyInjectionEvent, owner_id)),
            ExportCollection("context_events", _direct(ContextEvent, owner_id)),
            ExportCollection("blood_pressure", _direct(BloodPressureEvent, owner_id)),
            ExportCollection("weight", _direct(WeightEvent, owner_id)),
            ExportCollection("temperature", _direct(TemperatureEvent, owner_id)),
            ExportCollection("garmin_import_batches", _direct(GarminImportBatch, owner_id)),
            ExportCollection("garmin_metrics", _direct(GarminMetricEvent, owner_id)),
            ExportCollection("garmin_sleep", _direct(GarminSleepEvent, owner_id)),
            ExportCollection(
                "garmin_sleep_stage_intervals",
                _child(GarminSleepStageInterval, GarminSleepEvent, "sleep_event_id", owner_id),
            ),
            ExportCollection("garmin_activities", _direct(GarminActivityEvent, owner_id)),
            ExportCollection("lab_documents", _direct(LabDocument, owner_id)),
            ExportCollection("lab_panels", _direct(LabPanel, owner_id)),
            ExportCollection("lab_results", _direct(LabResult, owner_id)),
        ],
        "integrations": [
            *saved_locations,
            ExportCollection("garmin_connection_state", _direct(GarminConnection, owner_id)),
            ExportCollection("garmin_sync_runs", _direct(GarminSyncRun, owner_id)),
            ExportCollection("wearable_daily_summaries", _direct(WearableDailySummary, owner_id)),
        ],
        "ai": ai_rows,
        "reports": [
            ExportCollection("snapshots", report_snapshots),
            ExportCollection("artifacts", report_artifacts),
        ],
    }


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": base64.b64encode(value).decode("ascii")}
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, datetime | date | time):
        return value.isoformat()
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, Enum):
        return value.value
    if isinstance(value, Range):
        return {
            "lower": _jsonable(value.lower),
            "upper": _jsonable(value.upper),
            "bounds": value.bounds,
            "empty": value.isempty,
        }
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    return str(value)


def _compact(value: Any) -> str:
    return json.dumps(_jsonable(value), ensure_ascii=False, separators=(",", ":"))


def _row_count(session: Session, statement: Select[Any]) -> int:
    count = session.scalar(select(func.count()).select_from(statement.order_by(None).subquery()))
    return count or 0


def _rows(session: Session, statement: Select[Any]) -> Iterator[Mapping[str, Any]]:
    result = session.execute(statement.execution_options(yield_per=_BATCH_SIZE))
    for row in result.mappings():
        yield {str(key): value for key, value in row.items()}


def _write_pdf_source(
    writer: AtomicExportWriter, *, layout: DocumentLayout, document: Mapping[str, Any]
) -> None:
    document_id = uuid.UUID(str(document["id"]))
    path = layout.path("stored", document_id)
    writer.write(
        _compact(
            {
                "document_id": document_id,
                "display_name": document["display_name"],
                "media_type": document["media_type"],
                "sha256": document["sha256"],
                "byte_size": document["byte_size"],
                "encoding": "base64",
            }
        )[:-1]
    )
    writer.write(',"data":"')
    digest = hashlib.sha256()
    size = 0
    carry = b""
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                payload = carry + chunk
                complete = len(payload) - (len(payload) % 3)
                if complete:
                    writer.write(base64.b64encode(payload[:complete]))
                carry = payload[complete:]
            if carry:
                writer.write(base64.b64encode(carry))
    except OSError as exc:
        raise JobQueueError("export_source_document_unavailable") from exc
    if size != document["byte_size"] or digest.hexdigest() != document["sha256"]:
        raise JobQueueError("export_source_document_integrity_failed")
    writer.write('"}')


def _update_progress(
    factory: sessionmaker[Session], export_id: uuid.UUID, *, processed: int, total: int
) -> None:
    with factory() as session, session.begin():
        session.execute(
            update(PrivateExport)
            .where(PrivateExport.id == export_id, PrivateExport.completed_at.is_(None))
            .values(processed_rows=processed, total_rows=total)
        )


def generate(
    factory: sessionmaker[Session],
    *,
    root: Path,
    uploads: DocumentLayout,
    export_id: uuid.UUID,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> StoredExport:
    with factory() as lookup:
        export = lookup.get(PrivateExport, export_id)
        if export is None:
            raise JobQueueError("export_request_missing")
        owner_id = export.owner_id
        include_ai = export.include_ai
        include_sensitive = export.include_sensitive
        if export.relative_path and export.sha256 and export.byte_size:
            return StoredExport(export.relative_path, export.sha256, export.byte_size)

    writer = AtomicExportWriter(root, owner_id=owner_id, export_id=export_id)
    processed = 0
    try:
        with factory() as snapshot, snapshot.begin():
            snapshot.execute(text("SET TRANSACTION ISOLATION LEVEL REPEATABLE READ READ ONLY"))
            owner = (
                snapshot.execute(
                    select(
                        Owner.id,
                        Owner.email,
                        Owner.display_name,
                        Owner.default_timezone,
                        Owner.locale,
                        Owner.created_at,
                    ).where(Owner.id == owner_id)
                )
                .mappings()
                .one_or_none()
            )
            if owner is None:
                raise JobQueueError("export_owner_missing")
            groups = _collections(
                owner_id, include_ai=include_ai, include_sensitive=include_sensitive
            )
            source_documents = (
                select(
                    LabDocument.id,
                    LabDocument.display_name,
                    LabDocument.media_type,
                    LabDocument.sha256,
                    LabDocument.byte_size,
                )
                .where(
                    LabDocument.owner_id == owner_id,
                    LabDocument.status == LabDocumentStatus.STORED,
                )
                .order_by(LabDocument.id)
            )
            total = sum(
                _row_count(snapshot, collection.statement)
                for collections in groups.values()
                for collection in collections
            ) + _row_count(snapshot, source_documents)
            _update_progress(factory, export_id, processed=0, total=total)

            writer.write("{")
            writer.write('"export_version":2,')
            writer.write(f'"generated_at":{_compact(clock())},')
            writer.write(f'"owner":{_compact(dict(owner))},')
            writer.write(
                '"notice":'
                + _compact(
                    "Facts, physician-approved plans, AI content, integrations, and reports "
                    "remain separately labelled. Integration credentials, authentication "
                    "secrets, raw "
                    "provider responses, and queue internals are intentionally omitted."
                )
                + ","
            )
            group_items = list(groups.items())
            for group_index, (group_name, collections) in enumerate(group_items):
                writer.write(f'"{group_name}":{{')
                for collection_index, collection in enumerate(collections):
                    writer.write(f'"{collection.name}":[')
                    first = True
                    for row in _rows(snapshot, collection.statement):
                        if not first:
                            writer.write(",")
                        writer.write(_compact(dict(row)))
                        first = False
                        processed += 1
                        if processed % _PROGRESS_INTERVAL == 0:
                            _update_progress(factory, export_id, processed=processed, total=total)
                    writer.write("]")
                    if collection_index + 1 < len(collections) or group_name == "facts":
                        writer.write(",")
                if group_name == "facts":
                    writer.write('"lab_source_documents":[')
                    for index, document in enumerate(_rows(snapshot, source_documents)):
                        if index:
                            writer.write(",")
                        _write_pdf_source(writer, layout=uploads, document=document)
                        processed += 1
                        if processed % _PROGRESS_INTERVAL == 0:
                            _update_progress(factory, export_id, processed=processed, total=total)
                    writer.write("]")
                if group_name == "integrations":
                    writer.write(
                        ',"notice":'
                        + _compact(
                            "Non-secret Garmin state and provenance only; credentials, tokens, "
                            "and raw provider responses are omitted."
                        )
                    )
                if group_name == "reports":
                    writer.write(
                        ',"notice":'
                        + _compact(
                            "Report artifact metadata is included; report files remain available "
                            "as separate private downloads. Reports are excluded when sensitive "
                            "content is not requested because legacy snapshots have no reliable "
                            "sensitive-content flag."
                        )
                    )
                writer.write("}")
                if group_index + 1 < len(group_items):
                    writer.write(",")
            writer.write("}")
        stored = writer.finish()
    except Exception:
        writer.abort()
        raise

    completed_at = clock().astimezone(UTC)
    with factory() as session, session.begin():
        export = session.get(PrivateExport, export_id, with_for_update=True)
        if export is None:
            raise JobQueueError("export_request_missing")
        export.total_rows = total
        export.processed_rows = total
        export.relative_path = stored.relative_path
        export.sha256 = stored.sha256
        export.byte_size = stored.byte_size
        export.completed_at = completed_at
        audit.record(
            session,
            actor=audit.actor_for_owner(export.owner_id),
            action=audit.AuditAction.EXPORT_GENERATED,
            target_type="private_export",
            target_id=export.id,
            change_summary=(
                f"queued complete export;ai={export.include_ai};"
                f"sensitive={export.include_sensitive};rows={total}"
            ),
        )
    return stored
