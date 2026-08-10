"""Confirmation boundary for manual and CSV laboratory facts."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.timekeeping import EventTime
from healthcurve.labs.imports import LabCandidate, ParsedLabImport
from healthcurve.labs.models import LabPanel, LabResult
from healthcurve.labs.normalization import NORMALIZATION_VERSION, normalize_lab_value
from healthcurve.operations import audit


class LabConfirmationError(RuntimeError):
    pass


@dataclass(frozen=True)
class LabConfirmResult:
    panel: LabPanel
    created: bool
    result_count: int


def create_panel(
    session: Session,
    *,
    owner_id: uuid.UUID,
    specimen_time: EventTime,
    report_time: EventTime,
    candidates: list[LabCandidate] | tuple[LabCandidate, ...],
    source_type: SourceType,
    confirmation_state: ConfirmationState,
    provider_id: str | None = None,
    source_revision: str | None = None,
    laboratory_name: str | None = None,
    accession_id: str | None = None,
    specimen_type: str | None = None,
    report_status: str | None = None,
) -> LabPanel:
    if not candidates or any("missing_result" in candidate.flags for candidate in candidates):
        raise LabConfirmationError("lab_candidates_incomplete")
    panel = LabPanel(
        id=uuid.uuid4(),
        owner_id=owner_id,
        occurred_at=specimen_time.occurred_at,
        local_time=specimen_time.local_time,
        timezone=specimen_time.timezone,
        utc_offset_minutes=specimen_time.utc_offset_minutes,
        recorded_at=datetime.now(UTC),
        source_type=source_type,
        provider_id=provider_id,
        source_revision=source_revision,
        confirmation_state=confirmation_state,
        reported_at=report_time.occurred_at,
        reported_local_time=report_time.local_time,
        reported_timezone=report_time.timezone,
        reported_utc_offset_minutes=report_time.utc_offset_minutes,
        laboratory_name=laboratory_name,
        accession_id=accession_id,
        specimen_type=specimen_type,
        report_status=report_status,
    )
    session.add(panel)
    session.flush([panel])
    for candidate in candidates:
        normalized = normalize_lab_value(
            candidate.analyte_name, candidate.original_value, candidate.original_unit
        )
        session.add(
            LabResult(
                owner_id=owner_id,
                panel_id=panel.id,
                source_row_index=candidate.source_row_index,
                analyte_name=candidate.analyte_name,
                original_value=candidate.original_value,
                qualitative_result=candidate.qualitative_result,
                original_unit=candidate.original_unit,
                original_reference_range=candidate.original_reference_range,
                abnormal_flag=candidate.abnormal_flag,
                normalized_analyte_code=(
                    normalized.analyte_code
                    if normalized is not None
                    else candidate.normalized_analyte_code
                ),
                normalized_value=normalized.value if normalized is not None else None,
                normalized_unit=normalized.unit if normalized is not None else None,
                normalization_method=normalized.method if normalized is not None else None,
            )
        )
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.RECORD_CREATED,
        target_type="lab_panel",
        target_id=panel.id,
        change_summary=f"results={len(candidates)};source={source_type.value}",
    )
    session.flush()
    return panel


def confirm_csv(
    session: Session, *, owner_id: uuid.UUID, parsed: ParsedLabImport
) -> LabConfirmResult:
    lock_material = f"lab:{owner_id}:{parsed.source_sha256}".encode()
    if session.get_bind().dialect.name == "postgresql":
        lock_key = int.from_bytes(hashlib.sha256(lock_material).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    existing = session.scalar(
        select(LabPanel).where(
            LabPanel.owner_id == owner_id,
            LabPanel.source_type == SourceType.CSV_IMPORT,
            LabPanel.provider_id == parsed.source_sha256,
        )
    )
    if existing is not None:
        return LabConfirmResult(existing, False, len(existing.results))
    panel = create_panel(
        session,
        owner_id=owner_id,
        specimen_time=parsed.specimen_time,
        report_time=parsed.report_time,
        candidates=parsed.candidates,
        source_type=SourceType.CSV_IMPORT,
        confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
        provider_id=parsed.source_sha256,
        source_revision=parsed.mapping_sha256,
    )
    return LabConfirmResult(panel, True, len(parsed.candidates))


def backfill_normalizations(session: Session, *, owner_id: uuid.UUID) -> int:
    """Recompute only derived fields for existing source facts under the current version."""
    changed = 0
    for result in session.scalars(
        select(LabResult).where(LabResult.owner_id == owner_id).order_by(LabResult.id)
    ):
        normalized = normalize_lab_value(
            result.analyte_name, result.original_value, result.original_unit
        )
        if normalized is None:
            continue
        desired = (
            normalized.analyte_code,
            normalized.value,
            normalized.unit,
            normalized.method,
        )
        current = (
            result.normalized_analyte_code,
            result.normalized_value,
            result.normalized_unit,
            result.normalization_method,
        )
        if current == desired:
            continue
        (
            result.normalized_analyte_code,
            result.normalized_value,
            result.normalized_unit,
            result.normalization_method,
        ) = desired
        changed += 1
    if changed:
        audit.record(
            session,
            actor="system",
            action=audit.AuditAction.RECORD_CORRECTED,
            target_type="lab_normalization_derivation",
            target_id=owner_id,
            change_summary=f"derived_fields_recomputed;version={NORMALIZATION_VERSION};count={changed}",
        )
    session.flush()
    return changed


def manual_candidate(
    *,
    analyte_name: str,
    original_value: str | None,
    qualitative_result: str | None,
    original_unit: str | None,
    original_reference_range: str | None,
    abnormal_flag: str | None,
    normalized_analyte_code: str | None = None,
    normalized_value: Decimal | None = None,
    normalized_unit: str | None = None,
) -> LabCandidate:
    # Caller-supplied derived values remain forbidden. The deterministic registry runs
    # below and again at the fact-confirmation boundary.
    if normalized_value is not None or normalized_unit is not None:
        raise LabConfirmationError("manual_normalization_unsupported")
    candidate = LabCandidate(
        source_row_index=0,
        analyte_name=analyte_name,
        original_value=original_value,
        qualitative_result=qualitative_result,
        original_unit=original_unit,
        original_reference_range=original_reference_range,
        abnormal_flag=abnormal_flag,
        normalized_analyte_code=normalized_analyte_code,
    )
    normalized = normalize_lab_value(analyte_name, original_value, original_unit)
    if normalized is not None:
        candidate.normalized_analyte_code = normalized.analyte_code
        candidate.normalized_value = normalized.value
        candidate.normalized_unit = normalized.unit
        candidate.normalization_method = normalized.method
    return candidate
