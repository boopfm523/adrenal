"""Owner-scoped construction of canonical physician-report snapshot inputs."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Any, Final
from zoneinfo import ZoneInfo

from sqlalchemy import or_, select
from sqlalchemy.orm import Session

from healthcurve.ai.analysis import is_renderable_analysis
from healthcurve.ai.models import AIAnalysis
from healthcurve.analytics import service as analytics
from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events import service as events
from healthcurve.events.models import DiaryEvent, LifeEvent, SymptomEvent
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminMetricEvent,
    GarminSleepEvent,
)
from healthcurve.labs.models import LabPanel
from healthcurve.medications.models import DoseEvent, RegimenStatus, RegimenVersion
from healthcurve.reports.models import ReportSnapshot
from healthcurve.reports.service import create_snapshot
from healthcurve.vitals import service as vitals
from healthcurve.vitals.models import BloodPressureEvent, WeightEvent

SUPPORTED_SECTIONS: Final = frozenset(
    {
        "metrics",
        "doses",
        "episodes",
        "symptoms",
        "emergency_injections",
        "patient_notes",
        "life_events",
        "approved_plan",
        "labs",
        "wearables",
        "vitals",
    }
)


def _base_event(row: object) -> dict[str, object]:
    return {
        "id": str(row.id),  # type: ignore[attr-defined]
        "occurred_at": row.occurred_at,  # type: ignore[attr-defined]
        "local_time": row.local_time,  # type: ignore[attr-defined]
        "timezone": row.timezone,  # type: ignore[attr-defined]
        "source_type": row.source_type,  # type: ignore[attr-defined]
        "confirmation_state": row.confirmation_state,  # type: ignore[attr-defined]
        "notes": row.notes,  # type: ignore[attr-defined]
    }


def _current_events(
    session: Session,
    model: type[Any],
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[Any]:
    rows = list(
        session.scalars(
            select(model)
            .where(model.owner_id == owner_id, model.occurred_at >= start, model.occurred_at < end)
            .order_by(model.occurred_at, model.id)
        )
    )
    return events.current_only(session, model, rows)


def build_snapshot(
    session: Session,
    *,
    owner_id: uuid.UUID,
    date_from: date,
    date_to: date,
    timezone: str,
    selected_sections: list[str],
    include_ai: bool = False,
    include_sensitive: bool = False,
) -> ReportSnapshot:
    """Freeze the selected current records; no renderer re-queries these sources."""
    zone = ZoneInfo(timezone)
    start = datetime.combine(date_from, time.min, tzinfo=zone)
    end = datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
    content: dict[str, object] = {"fact": [], "plan": [], "patient_note": [], "ai": []}
    manifest: dict[str, list[str]] = {"fact": [], "plan": [], "patient_note": [], "ai": []}
    facts: list[dict[str, object]] = content["fact"]  # type: ignore[assignment]
    plans: list[dict[str, object]] = content["plan"]  # type: ignore[assignment]
    notes: list[dict[str, object]] = content["patient_note"]  # type: ignore[assignment]
    analyses: list[dict[str, object]] = content["ai"]  # type: ignore[assignment]

    if "doses" in selected_sections:
        for row in _current_events(session, DoseEvent, owner_id=owner_id, start=start, end=end):
            facts.append(
                {
                    **_base_event(row),
                    "record_type": "dose",
                    "medication_name": row.medication.name,
                    "amount": row.amount,
                    "unit": row.unit,
                    "route": row.route,
                    "category": row.category,
                }
            )
            manifest["fact"].append(str(row.id))

    if "symptoms" in selected_sections:
        for row in _current_events(session, SymptomEvent, owner_id=owner_id, start=start, end=end):
            facts.append(
                {
                    **_base_event(row),
                    "record_type": "symptom",
                    "name": row.name,
                    "severity": row.severity,
                    "body_area": row.body_area,
                    "ended_at": row.ended_at,
                }
            )
            manifest["fact"].append(str(row.id))

    if "vitals" in selected_sections:
        for row in _current_events(
            session, BloodPressureEvent, owner_id=owner_id, start=start, end=end
        ):
            facts.append(
                {
                    **_base_event(row),
                    "record_type": "blood_pressure",
                    "systolic_mmhg": row.systolic_mmhg,
                    "diastolic_mmhg": row.diastolic_mmhg,
                    "pulse_bpm": row.pulse_bpm,
                }
            )
            manifest["fact"].append(str(row.id))
        for row in _current_events(session, WeightEvent, owner_id=owner_id, start=start, end=end):
            facts.append(
                {
                    **_base_event(row),
                    "record_type": "weight",
                    "value": row.value,
                    "unit": row.unit,
                    "display_lb": vitals.display_weight_lb(row.value, row.unit),
                    "normalized_kg": row.normalized_kg,
                    "normalization_definition": "1 lb = 0.45359237 kg",
                    "presentation_definition": (
                        "Pounds are primary, rounded half up to 0.1 lb; original value and unit "
                        "are retained; 1 lb = 0.45359237 kg"
                    ),
                }
            )
            manifest["fact"].append(str(row.id))

    if "episodes" in selected_sections:
        rows = session.scalars(
            select(StressEpisode)
            .where(
                StressEpisode.owner_id == owner_id,
                StressEpisode.started_at >= start,
                StressEpisode.started_at < end,
            )
            .order_by(StressEpisode.started_at, StressEpisode.id)
        )
        for row in rows:
            facts.append(
                {
                    "id": str(row.id),
                    "record_type": "stress_episode",
                    "trigger": row.trigger,
                    "status": row.status,
                    "severity": row.severity,
                    "started_at": row.started_at,
                    "ended_at": row.ended_at,
                    "timezone": row.timezone,
                    "highest_temperature_c": row.highest_temperature_c,
                    "illness_description": row.illness_description,
                    "recovery_notes": row.recovery_notes,
                    "outcome": row.outcome,
                    "notes": row.notes,
                }
            )
            manifest["fact"].append(str(row.id))

    if "emergency_injections" in selected_sections:
        for row in _current_events(
            session, EmergencyInjectionEvent, owner_id=owner_id, start=start, end=end
        ):
            facts.append(
                {
                    **_base_event(row),
                    "record_type": "emergency_injection",
                    "amount": row.amount,
                    "unit": row.unit,
                    "route": row.route,
                    "reason": row.reason,
                    "emergency_services_called": row.emergency_services_called,
                    "transported_to_hospital": row.transported_to_hospital,
                    "response": row.response,
                }
            )
            manifest["fact"].append(str(row.id))

    if "patient_notes" in selected_sections:
        for row in _current_events(session, DiaryEvent, owner_id=owner_id, start=start, end=end):
            if row.is_sensitive and not include_sensitive:
                continue
            notes.append(
                {
                    **_base_event(row),
                    "record_type": "patient_note",
                    "text": row.text,
                    "tags": row.tags,
                    "is_sensitive": row.is_sensitive,
                }
            )
            manifest["patient_note"].append(str(row.id))

    if "life_events" in selected_sections:
        for row in _current_events(session, LifeEvent, owner_id=owner_id, start=start, end=end):
            if row.is_sensitive and not include_sensitive:
                continue
            facts.append(
                {
                    **_base_event(row),
                    "record_type": "life_event",
                    "title": row.title,
                    "category": row.category,
                    "description": row.description,
                    "ended_at": row.ended_at,
                    "association_caution": (
                        "Context only; temporal proximity does not establish causation."
                    ),
                }
            )
            manifest["fact"].append(str(row.id))

    if "labs" in selected_sections:
        panels = session.scalars(
            select(LabPanel)
            .where(
                LabPanel.owner_id == owner_id,
                LabPanel.occurred_at >= start,
                LabPanel.occurred_at < end,
            )
            .order_by(LabPanel.occurred_at, LabPanel.id)
        )
        for panel in events.current_only(session, LabPanel, list(panels)):
            facts.append(
                {
                    **_base_event(panel),
                    "record_type": "lab_panel",
                    "laboratory_name": panel.laboratory_name,
                    "specimen_type": panel.specimen_type,
                    "reported_at": panel.reported_at,
                    "report_status": panel.report_status,
                    "results": [
                        {
                            "id": str(result.id),
                            "source_document_id": (
                                str(result.source_document_id)
                                if result.source_document_id is not None
                                else None
                            ),
                            "source_page_number": result.source_page_number,
                            "analyte_name": result.analyte_name,
                            "original_value": result.original_value,
                            "qualitative_result": result.qualitative_result,
                            "original_unit": result.original_unit,
                            "original_reference_range": result.original_reference_range,
                            "abnormal_flag": result.abnormal_flag,
                            "normalized_analyte_code": result.normalized_analyte_code,
                            "normalized_value": result.normalized_value,
                            "normalized_unit": result.normalized_unit,
                            "normalization_method": result.normalization_method,
                        }
                        for result in panel.results
                    ],
                }
            )
            manifest["fact"].extend([str(panel.id), *(str(result.id) for result in panel.results)])

    if "wearables" in selected_sections:
        for model, record_type, fields in (
            (
                GarminMetricEvent,
                "garmin_metric",
                (
                    "metric_type",
                    "value",
                    "unit",
                    "period_end_at",
                    "aggregation",
                    "sample_interval_seconds",
                    "garmin_field_name",
                ),
            ),
            (
                GarminSleepEvent,
                "garmin_sleep",
                (
                    "ended_at",
                    "duration_seconds",
                    "garmin_duration_source",
                    "awakenings",
                    "overall_sleep_score",
                ),
            ),
            (
                GarminActivityEvent,
                "garmin_activity",
                (
                    "ended_at",
                    "sport",
                    "title",
                    "elapsed_seconds",
                    "distance_miles",
                    "average_heart_rate",
                    "maximum_heart_rate",
                ),
            ),
        ):
            for row in _current_events(session, model, owner_id=owner_id, start=start, end=end):
                facts.append(
                    {
                        **_base_event(row),
                        "record_type": record_type,
                        "source": "Garmin",
                        **{field: getattr(row, field) for field in fields},
                    }
                )
                manifest["fact"].append(str(row.id))

    if "approved_plan" in selected_sections:
        local_start = datetime.combine(date_from, time.min)
        local_end = datetime.combine(date_to + timedelta(days=1), time.min)
        regimens = session.scalars(
            select(RegimenVersion)
            .where(
                RegimenVersion.owner_id == owner_id,
                RegimenVersion.status == RegimenStatus.APPROVED,
                RegimenVersion.effective_from < local_end,
                or_(
                    RegimenVersion.effective_to.is_(None), RegimenVersion.effective_to > local_start
                ),
            )
            .order_by(RegimenVersion.effective_from, RegimenVersion.id)
        )
        for regimen in regimens:
            plans.append(
                {
                    "id": str(regimen.id),
                    "record_type": "approved_regimen",
                    "version_label": regimen.version_label,
                    "effective_from": regimen.effective_from,
                    "effective_to": regimen.effective_to,
                    "approved_at": regimen.approved_at,
                    "approved_by": regimen.approved_by,
                    "approval_source": regimen.approval_source,
                    "slots": [
                        {
                            "id": str(slot.id),
                            "medication_name": slot.medication.name,
                            "scheduled_local_time": slot.scheduled_local_time.isoformat(),
                            "amount": slot.amount,
                            "unit": slot.unit,
                            "route": slot.route,
                            "condition": slot.condition,
                        }
                        for slot in sorted(
                            regimen.slots,
                            key=lambda slot: (slot.sort_order, slot.scheduled_local_time),
                        )
                    ],
                    "instructions": [
                        {
                            "id": str(instruction.id),
                            "category": instruction.category,
                            "title": instruction.title,
                            "body": instruction.body,
                            "authored_by": instruction.authored_by,
                            "authored_on": instruction.authored_on,
                        }
                        for instruction in sorted(
                            regimen.instructions, key=lambda instruction: instruction.sort_order
                        )
                    ],
                }
            )
            manifest["plan"].extend(
                [
                    str(regimen.id),
                    *(str(slot.id) for slot in regimen.slots),
                    *(str(instruction.id) for instruction in regimen.instructions),
                ]
            )

    if include_ai:
        rows = session.scalars(
            select(AIAnalysis)
            .where(
                AIAnalysis.owner_id == owner_id,
                AIAnalysis.hidden_at.is_(None),
                or_(AIAnalysis.range_start.is_(None), AIAnalysis.range_start < end),
                or_(AIAnalysis.range_end.is_(None), AIAnalysis.range_end >= start),
            )
            .order_by(AIAnalysis.generated_at, AIAnalysis.id)
        )
        for row in rows:
            if not is_renderable_analysis(row):
                continue
            analyses.append(
                {
                    "id": str(row.id),
                    "record_type": "ai_analysis",
                    "analysis_type": row.analysis_type,
                    "body": row.body,
                    "source_record_ids": row.source_record_ids,
                    "computed_inputs": row.computed_inputs,
                    "model_name": row.model_name,
                    "model_digest": row.model_digest,
                    "prompt_version": row.prompt_version,
                    "schema_version": row.schema_version,
                    "generated_at": row.generated_at,
                }
            )
            manifest["ai"].append(str(row.id))

    summary = (
        analytics.summary_for_owner(
            session,
            owner_id=owner_id,
            date_from=date_from,
            date_to=date_to,
            timezone=timezone,
        )
        if "metrics" in selected_sections
        else {}
    )
    metric_values = {
        name: value
        for name, value in summary.items()
        if name in {"daily_doses", "timing", "episodes", "symptoms"}
    }
    return create_snapshot(
        session,
        owner_id=owner_id,
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        selected_sections=selected_sections,
        source_manifest=manifest,
        metric_values=metric_values,
        snapshot_content=content,
        include_ai=include_ai,
    )
