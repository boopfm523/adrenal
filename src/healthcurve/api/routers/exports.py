"""Complete data export (plan section 12).

The export separates the three categories into labelled sections (SAFE-07) and
excludes AI content unless explicitly asked for. Integration credentials are never
included, and the export says so rather than omitting them silently (classification
rule 4).
"""

from __future__ import annotations

import json
from base64 import b64encode
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from healthcurve.api.deps import CurrentOwner, DbSession
from healthcurve.context.models import ContextEvent
from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events.models import DiaryEvent, LifeEvent, SymptomEvent
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminConnection,
    GarminImportBatch,
    GarminMetricEvent,
    GarminSleepEvent,
    GarminSyncRun,
)
from healthcurve.medications.models import DoseEvent, Medication, RegimenVersion
from healthcurve.operations import audit
from healthcurve.reports.models import ReportArtifact, ReportSnapshot
from healthcurve.vitals.models import BloodPressureEvent, WeightEvent

router = APIRouter(tags=["exports"])


def _rows(session: DbSession, model: type, owner_id: Any) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for row in session.scalars(select(model).where(model.owner_id == owner_id)):
        record: dict[str, Any] = {}
        for column in model.__table__.columns:
            value = getattr(row, column.name)
            record[column.name] = _jsonable(value)
        out.append(record)
    return out


def _jsonable(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, bytes):
        return {"encoding": "base64", "data": b64encode(value).decode("ascii")}
    if isinstance(value, list | tuple):
        return [_jsonable(item) for item in value]
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    return str(value)


def _report_export(session: DbSession, owner_id: Any, *, include_ai: bool) -> dict[str, Any]:
    statement = select(ReportSnapshot).where(ReportSnapshot.owner_id == owner_id)
    if not include_ai:
        statement = statement.where(ReportSnapshot.include_ai.is_(False))
    snapshots = list(session.scalars(statement))
    snapshot_ids = {snapshot.id for snapshot in snapshots}
    artifacts = [
        {
            "id": str(artifact.id),
            "snapshot_id": str(artifact.snapshot_id),
            "format": artifact.format,
            "media_type": artifact.media_type,
            "sha256": artifact.sha256,
            "byte_size": artifact.byte_size,
            "created_at": _jsonable(artifact.created_at),
        }
        for artifact in session.scalars(
            select(ReportArtifact).where(ReportArtifact.owner_id == owner_id)
        )
        if artifact.snapshot_id in snapshot_ids
    ]
    return {
        "snapshots": [
            {
                column.name: _jsonable(getattr(snapshot, column.name))
                for column in ReportSnapshot.__table__.columns
            }
            for snapshot in snapshots
        ],
        "artifacts": artifacts,
        "notice": (
            "AI-enabled report snapshots are excluded unless AI export was explicitly requested. "
            "Artifact file bytes are downloaded separately from the Reports page."
        ),
    }


@router.post("/exports")
def create_export(
    session: DbSession,
    owner: CurrentOwner,
    include_ai: bool = Query(
        default=False,
        description="AI analysis is excluded by default (SAFE-07). Opt in explicitly.",
    ),
    include_sensitive: bool = Query(default=True),
) -> StreamingResponse:
    payload: dict[str, Any] = {
        "export_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "owner": {"email": owner.email, "default_timezone": owner.default_timezone},
        "notice": (
            "Sections are separated by category. 'facts' are what you recorded, "
            "'plan' is physician-approved content, and 'ai' is generated analysis "
            "which is excluded unless you asked for it. Integration credentials are "
            "intentionally omitted and cannot be exported."
        ),
        "plan": {
            "medications": _rows(session, Medication, owner.id),
            "regimen_versions": _rows(session, RegimenVersion, owner.id),
        },
        "facts": {
            "doses": _rows(session, DoseEvent, owner.id),
            "symptoms": _rows(session, SymptomEvent, owner.id),
            "diary_events": [
                r
                for r in _rows(session, DiaryEvent, owner.id)
                if include_sensitive or not r.get("is_sensitive")
            ],
            "life_events": _rows(session, LifeEvent, owner.id),
            "stress_episodes": _rows(session, StressEpisode, owner.id),
            "emergency_injections": _rows(session, EmergencyInjectionEvent, owner.id),
            "context_events": _rows(session, ContextEvent, owner.id),
            "blood_pressure": _rows(session, BloodPressureEvent, owner.id),
            "weight": _rows(session, WeightEvent, owner.id),
            "garmin_import_batches": _rows(session, GarminImportBatch, owner.id),
            "garmin_metrics": _rows(session, GarminMetricEvent, owner.id),
            "garmin_sleep": _rows(session, GarminSleepEvent, owner.id),
            "garmin_activities": _rows(session, GarminActivityEvent, owner.id),
        },
        "integrations": {
            "garmin": {
                "connection_state": _rows(session, GarminConnection, owner.id),
                "sync_runs": _rows(session, GarminSyncRun, owner.id),
                "notice": (
                    "This section contains non-secret sync provenance only. Garmin "
                    "credentials, refresh tokens, and raw provider responses are excluded."
                ),
            }
        },
        "ai": {} if not include_ai else {"note": "AI analysis included at your request"},
        "reports": _report_export(session, owner.id, include_ai=include_ai),
    }

    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.EXPORT_GENERATED,
        change_summary=f"include_ai={include_ai}",
    )

    body = json.dumps(payload, indent=2)
    filename = f"healthcurve-export-{datetime.now(UTC):%Y%m%d}.json"
    return StreamingResponse(
        iter([body]),
        media_type="application/json",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "Cache-Control": "no-store",
        },
    )
