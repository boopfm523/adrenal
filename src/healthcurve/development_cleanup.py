"""Fail-closed development cleanup of the legacy synthetic medication bootstrap.

This is deliberately not a general plan-deletion service.  The only accepted target
is an exact, versioned fingerprint of the sample emitted by the old medication YAML
template.  A plan that merely contains words such as ``Example`` or ``synthetic`` is
not evidence and will never match.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime, time
from decimal import Decimal
from typing import Any, Final

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthcurve.ai.models import AIAnalysis, ExtractionDraft
from healthcurve.episodes.models import EmergencyInjectionEvent
from healthcurve.labs.models import LabDocument
from healthcurve.medications.models import (
    ApprovedInstruction,
    DoseEvent,
    Medication,
    RegimenDoseSlot,
    RegimenStatus,
    RegimenVersion,
)
from healthcurve.operations import audit
from healthcurve.reports.models import ReportSnapshot

PROFILE_VERSION: Final = "legacy-medications-template-v1"
_LEGACY_EFFECTIVE_FROM: Final = datetime(2026, 1, 1, tzinfo=UTC).replace(tzinfo=None)


class SyntheticBootstrapCleanupError(RuntimeError):
    """The database does not prove that the requested cleanup is safe."""


@dataclass(frozen=True, slots=True)
class CleanupCounts:
    regimen_versions: int
    regimen_dose_slots: int
    approved_instructions: int
    medications: int


@dataclass(frozen=True, slots=True)
class ReferenceCounts:
    doses: int = 0
    injections: int = 0
    other_plan_slots: int = 0
    reports: int = 0
    analyses: int = 0
    extraction_drafts: int = 0
    documents: int = 0

    @property
    def total(self) -> int:
        return sum(
            (
                self.doses,
                self.injections,
                self.other_plan_slots,
                self.reports,
                self.analyses,
                self.extraction_drafts,
                self.documents,
            )
        )


@dataclass(frozen=True, slots=True)
class CleanupPreview:
    profile_version: str
    regimen_version_ids: tuple[uuid.UUID, ...]
    regimen_dose_slot_ids: tuple[uuid.UUID, ...]
    approved_instruction_ids: tuple[uuid.UUID, ...]
    medication_ids: tuple[uuid.UUID, ...]
    counts: CleanupCounts
    references: ReferenceCounts
    confirmation_phrase: str


_MEDICATION_SIGNATURES: Final = (
    ("hydrocortisone", "tablet", Decimal("10.0000"), "mg", "mg", "oral"),
    ("fludrocortisone", "tablet", Decimal("0.1000"), "mg", "mg", "oral"),
    (
        "hydrocortisone sodium succinate",
        "injection",
        Decimal("100.0000"),
        "mg",
        "mg",
        "intramuscular",
    ),
)

_SLOT_SIGNATURES: Final = (
    (_MEDICATION_SIGNATURES[1], time(7, 0), Decimal("0.1000"), "mg", "oral", None, 0),
    (_MEDICATION_SIGNATURES[0], time(7, 0), Decimal("10.0000"), "mg", "oral", None, 0),
    (_MEDICATION_SIGNATURES[0], time(12, 30), Decimal("5.0000"), "mg", "oral", None, 0),
    (_MEDICATION_SIGNATURES[0], time(17, 0), Decimal("2.5000"), "mg", "oral", None, 0),
)

_INSTRUCTION_SIGNATURES: Final = (
    (
        "emergency",
        "Emergency injection",
        "Replace with the exact wording your physician gave you.\n",
        "Dr Example, Endocrinology",
        date(2026, 1, 1),
        0,
    ),
    (
        "illness",
        "Sick day rules",
        "Replace with the exact wording your physician gave you.\n",
        "Dr Example, Endocrinology",
        date(2026, 1, 1),
        0,
    ),
)


def preview_synthetic_bootstrap(session: Session, *, owner_id: uuid.UUID) -> CleanupPreview:
    """Return one exact cleanup target or fail without changing the session."""
    candidates = list(
        session.scalars(
            select(RegimenVersion)
            .where(RegimenVersion.owner_id == owner_id)
            .order_by(RegimenVersion.created_at, RegimenVersion.id)
            .with_for_update()
        ).unique()
    )
    medications = list(
        session.scalars(
            select(Medication)
            .where(Medication.owner_id == owner_id)
            .order_by(Medication.id)
            .with_for_update()
        )
    )
    matching_medications = _match_medications(medications)
    matching_regimens = [row for row in candidates if _matches_legacy_regimen(row)]
    if len(matching_regimens) != 1 or matching_medications is None:
        raise SyntheticBootstrapCleanupError(
            "no single exact legacy synthetic medication bootstrap was found; nothing changed"
        )

    regimen = matching_regimens[0]
    target_medications = tuple(sorted(matching_medications, key=lambda row: str(row.id)))
    target_slots = tuple(sorted(regimen.slots, key=lambda row: str(row.id)))
    target_instructions = tuple(sorted(regimen.instructions, key=lambda row: str(row.id)))
    target_ids = {
        str(regimen.id),
        *(str(row.id) for row in target_medications),
        *(str(row.id) for row in target_slots),
        *(str(row.id) for row in target_instructions),
    }
    references = _reference_counts(
        session,
        owner_id=owner_id,
        regimen=regimen,
        medications=target_medications,
        slots=target_slots,
        target_ids=target_ids,
    )
    token_material = "|".join((PROFILE_VERSION, *sorted(target_ids))).encode()
    token = hashlib.sha256(token_material).hexdigest()[:12].upper()
    return CleanupPreview(
        profile_version=PROFILE_VERSION,
        regimen_version_ids=(regimen.id,),
        regimen_dose_slot_ids=tuple(row.id for row in target_slots),
        approved_instruction_ids=tuple(row.id for row in target_instructions),
        medication_ids=tuple(row.id for row in target_medications),
        counts=CleanupCounts(
            regimen_versions=1,
            regimen_dose_slots=len(target_slots),
            approved_instructions=len(target_instructions),
            medications=len(target_medications),
        ),
        references=references,
        confirmation_phrase=f"PURGE SYNTHETIC BOOTSTRAP {token}",
    )


def execute_synthetic_bootstrap_cleanup(
    session: Session,
    *,
    owner_id: uuid.UUID,
    preview: CleanupPreview,
    confirmation: str,
) -> CleanupCounts:
    """Delete the exact previewed target in the caller's transaction."""
    current = preview_synthetic_bootstrap(session, owner_id=owner_id)
    if current != preview:
        raise SyntheticBootstrapCleanupError(
            "the cleanup target changed after preview; run a new preview; nothing changed"
        )
    if current.references.total:
        raise SyntheticBootstrapCleanupError(
            "the exact synthetic bootstrap has retained references; nothing changed "
            f"({_reference_summary(current.references)})"
        )
    if confirmation.strip() != current.confirmation_phrase:
        raise SyntheticBootstrapCleanupError("confirmation did not match; nothing changed")

    regimen = session.get(RegimenVersion, current.regimen_version_ids[0])
    medications = [session.get(Medication, row_id) for row_id in current.medication_ids]
    if regimen is None or any(row is None for row in medications):
        raise SyntheticBootstrapCleanupError(
            "the cleanup target changed after preview; run a new preview; nothing changed"
        )
    session.delete(regimen)
    for medication in medications:
        assert medication is not None  # narrowed by the guard above
        session.delete(medication)
    try:
        session.flush()
    except IntegrityError as exc:
        raise SyntheticBootstrapCleanupError(
            "a retained database reference prevented cleanup; transaction must be rolled back"
        ) from exc
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.SYNTHETIC_MEDICATION_BOOTSTRAP_PURGED,
        target_type="synthetic_medication_bootstrap",
        target_id=current.regimen_version_ids[0],
        change_summary=(
            f"profile={PROFILE_VERSION}; regimen_versions={current.counts.regimen_versions}; "
            f"slots={current.counts.regimen_dose_slots}; "
            f"instructions={current.counts.approved_instructions}; "
            f"medications={current.counts.medications}"
        ),
    )
    return current.counts


def _match_medications(rows: list[Medication]) -> tuple[Medication, ...] | None:
    matches: list[Medication] = []
    for signature in _MEDICATION_SIGNATURES:
        found = [row for row in rows if _medication_signature(row) == signature]
        if len(found) != 1:
            return None
        matches.append(found[0])
    return tuple(matches)


def _medication_signature(row: Medication) -> tuple[Any, ...]:
    return (
        (
            row.normalized_name,
            row.formulation,
            row.strength,
            row.strength_unit,
            _enum_value(row.default_unit),
            _enum_value(row.default_route),
        )
        if row.active_from is None and row.active_to is None and row.notes is None
        else ()
    )


def _matches_legacy_regimen(row: RegimenVersion) -> bool:
    if (
        row.version_label != "2026 replacement schedule"
        or row.effective_from != _LEGACY_EFFECTIVE_FROM
        or row.effective_to is not None
        or row.notes is not None
        or row.retired_at is not None
    ):
        return False
    if row.status is RegimenStatus.DRAFT:
        if row.approved_at or row.approved_by or row.approval_source:
            return False
    elif row.status is RegimenStatus.APPROVED:
        if (
            row.approved_at is None
            or row.approved_by != "Dr Example, Endocrinology"
            or row.approval_source != "clinic letter 2026-01-01"
        ):
            return False
    else:
        return False
    return tuple(sorted((_slot_signature(slot) for slot in row.slots), key=str)) == tuple(
        sorted(_SLOT_SIGNATURES, key=str)
    ) and tuple(
        sorted((_instruction_signature(item) for item in row.instructions), key=str)
    ) == tuple(sorted(_INSTRUCTION_SIGNATURES, key=str))


def _slot_signature(row: RegimenDoseSlot) -> tuple[Any, ...]:
    return (
        _medication_signature(row.medication),
        row.scheduled_local_time,
        row.amount,
        _enum_value(row.unit),
        _enum_value(row.route),
        row.condition,
        row.sort_order,
    )


def _instruction_signature(row: ApprovedInstruction) -> tuple[Any, ...]:
    return (
        _enum_value(row.category),
        row.title,
        row.body,
        row.authored_by,
        row.authored_on,
        row.sort_order,
    )


def _reference_counts(
    session: Session,
    *,
    owner_id: uuid.UUID,
    regimen: RegimenVersion,
    medications: tuple[Medication, ...],
    slots: tuple[RegimenDoseSlot, ...],
    target_ids: set[str],
) -> ReferenceCounts:
    medication_ids = tuple(row.id for row in medications)
    slot_ids = tuple(row.id for row in slots)
    doses = (
        session.scalar(
            select(func.count())
            .select_from(DoseEvent)
            .where(
                DoseEvent.owner_id == owner_id,
                or_(
                    DoseEvent.regimen_version_id == regimen.id,
                    DoseEvent.slot_id.in_(slot_ids),
                    DoseEvent.medication_id.in_(medication_ids),
                ),
            )
        )
        or 0
    )
    injections = (
        session.scalar(
            select(func.count())
            .select_from(EmergencyInjectionEvent)
            .where(
                EmergencyInjectionEvent.owner_id == owner_id,
                EmergencyInjectionEvent.medication_id.in_(medication_ids),
            )
        )
        or 0
    )
    other_plan_slots = (
        session.scalar(
            select(func.count())
            .select_from(RegimenDoseSlot)
            .where(
                RegimenDoseSlot.regimen_version_id != regimen.id,
                RegimenDoseSlot.medication_id.in_(medication_ids),
            )
        )
        or 0
    )
    reports = sum(
        _contains_target(row.source_manifest, target_ids)
        or _contains_target(row.snapshot_content, target_ids)
        for row in session.scalars(
            select(ReportSnapshot).where(ReportSnapshot.owner_id == owner_id)
        )
    )
    analyses = sum(
        _contains_target(row.source_record_ids, target_ids)
        or _contains_target(row.computed_inputs, target_ids)
        for row in session.scalars(select(AIAnalysis).where(AIAnalysis.owner_id == owner_id))
    )
    extraction_drafts = sum(
        _contains_target(row.candidates, target_ids)
        or _contains_target(row.original_candidates, target_ids)
        or _contains_target(row.created_event_ids, target_ids)
        for row in session.scalars(
            select(ExtractionDraft).where(ExtractionDraft.owner_id == owner_id)
        )
    )
    documents = 0
    if regimen.source_document_checksum is not None:
        matching_documents = (
            session.scalar(
                select(func.count())
                .select_from(LabDocument)
                .where(
                    LabDocument.owner_id == owner_id,
                    LabDocument.sha256 == regimen.source_document_checksum,
                )
            )
            or 0
        )
        # The checksum on the plan is itself a retained source-document reference,
        # even if its document row was already removed or came from another store.
        documents = max(1, matching_documents)
    return ReferenceCounts(
        doses=doses,
        injections=injections,
        other_plan_slots=other_plan_slots,
        reports=reports,
        analyses=analyses,
        extraction_drafts=extraction_drafts,
        documents=documents,
    )


def _contains_target(value: object, target_ids: set[str]) -> bool:
    if isinstance(value, str):
        return value in target_ids
    if isinstance(value, dict):
        return any(_contains_target(item, target_ids) for item in value.values())
    if isinstance(value, (list, tuple)):
        return any(_contains_target(item, target_ids) for item in value)
    return False


def _enum_value(value: object) -> object:
    """Return a StrEnum's stable value while tolerating freshly assigned strings."""
    return getattr(value, "value", value)


def _reference_summary(references: ReferenceCounts) -> str:
    fields = (
        ("doses", references.doses),
        ("injections", references.injections),
        ("other_plan_slots", references.other_plan_slots),
        ("reports", references.reports),
        ("analyses", references.analyses),
        ("extraction_drafts", references.extraction_drafts),
        ("documents", references.documents),
    )
    return ", ".join(f"{name}={count}" for name, count in fields if count)
