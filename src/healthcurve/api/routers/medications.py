"""Medications and physician-approved regimen versions.

Approval is the sensitive operation here. It requires an authenticated human, a CSRF
token, an approver, and a source (SAFE-16), and it is audited.
"""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.schemas import (
    DoseSlotOut,
    InstructionOut,
    MedicationIn,
    MedicationOut,
    RegimenApprovalIn,
    RegimenVersionIn,
    RegimenVersionOut,
)
from healthcurve.medications import service
from healthcurve.medications.models import (
    ApprovedInstruction,
    Medication,
    RegimenDoseSlot,
    RegimenStatus,
    RegimenVersion,
)
from healthcurve.operations import audit

router = APIRouter(tags=["medications"])


# ---------------------------------------------------------------------------
# Medications
# ---------------------------------------------------------------------------


@router.get("/medications", response_model=list[MedicationOut])
def list_medications(session: DbSession, owner: CurrentOwner, include_inactive: bool = True):
    query = select(Medication).where(Medication.owner_id == owner.id).order_by(Medication.name)
    return [_medication_out(m) for m in session.scalars(query)]


@router.post(
    "/medications",
    response_model=MedicationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_medication(payload: MedicationIn, session: DbSession, owner: CurrentOwner):
    existing = service.find_medication_by_name(session, owner.id, payload.name)
    if existing is not None and existing.strength == payload.strength:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="a medication with this name and strength already exists",
        )

    medication = Medication(
        owner_id=owner.id,
        name=payload.name.strip(),
        normalized_name=service.normalize_name(payload.name),
        formulation=payload.formulation,
        strength=payload.strength,
        strength_unit=payload.strength_unit,
        default_unit=payload.default_unit,
        default_route=payload.default_route,
        active_from=payload.active_from,
        active_to=payload.active_to,
        notes=payload.notes,
    )
    session.add(medication)
    session.flush()
    return _medication_out(medication)


def _medication_out(m: Medication) -> MedicationOut:
    return MedicationOut(
        id=m.id,
        name=m.name,
        formulation=m.formulation,
        strength=m.strength,
        strength_unit=m.strength_unit,
        default_unit=m.default_unit,
        default_route=m.default_route,
        active_from=m.active_from,
        active_to=m.active_to,
        notes=m.notes,
    )


# ---------------------------------------------------------------------------
# Regimen versions
# ---------------------------------------------------------------------------


@router.get("/regimens", response_model=list[RegimenVersionOut])
def list_regimens(
    session: DbSession, owner: CurrentOwner, status_filter: RegimenStatus | None = None
):
    query = select(RegimenVersion).where(RegimenVersion.owner_id == owner.id)
    if status_filter is not None:
        query = query.where(RegimenVersion.status == status_filter)
    query = query.order_by(RegimenVersion.effective_from.desc())
    return [_regimen_out(v) for v in session.scalars(query)]


@router.get("/regimens/active", response_model=RegimenVersionOut | None)
def active_regimen(session: DbSession, owner: CurrentOwner):
    """The approved version in force now, or null.

    Null is a real answer and the UI must say "no approved plan" rather than falling
    back to the newest draft.
    """
    from datetime import UTC, datetime

    version = service.active_version_at(session, owner.id, datetime.now(UTC))
    return _regimen_out(version) if version else None


@router.post(
    "/regimens",
    response_model=RegimenVersionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_regimen(payload: RegimenVersionIn, session: DbSession, owner: CurrentOwner):
    """Create a *draft*. Drafts are never in force; approval is a separate act."""
    try:
        version = service.create_draft(
            session,
            owner_id=owner.id,
            version_label=payload.version_label,
            effective_from=payload.effective_from,
            effective_to=payload.effective_to,
            notes=payload.notes,
        )
    except service.PlanError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc

    for slot in payload.slots:
        medication = session.get(Medication, slot.medication_id)
        if medication is None or medication.owner_id != owner.id:
            raise HTTPException(
                status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
                detail=f"unknown medication {slot.medication_id}",
            )
        session.add(
            RegimenDoseSlot(
                regimen_version_id=version.id,
                medication_id=slot.medication_id,
                scheduled_local_time=slot.scheduled_local_time,
                amount=slot.amount,
                unit=slot.unit,
                route=slot.route,
                condition=slot.condition,
                sort_order=slot.sort_order,
            )
        )

    for instruction in payload.instructions:
        session.add(
            ApprovedInstruction(
                regimen_version_id=version.id,
                category=instruction.category,
                title=instruction.title,
                body=instruction.body,
                authored_by=instruction.authored_by,
                authored_on=instruction.authored_on,
                sort_order=instruction.sort_order,
            )
        )

    session.flush()
    session.refresh(version)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.REGIMEN_DRAFTED,
        target_type="regimen_version",
        target_id=version.id,
    )
    return _regimen_out(version)


@router.post(
    "/regimens/{version_id}/approve",
    response_model=RegimenVersionOut,
    dependencies=[Depends(require_csrf)],
)
def approve_regimen(
    version_id: uuid.UUID,
    payload: RegimenApprovalIn,
    session: DbSession,
    owner: CurrentOwner,
):
    """Record a physician's approval.

    This endpoint is reachable only by an authenticated human with a CSRF token. There
    is no AI-callable path to it, and the AI database role could not write the row
    anyway (SAFE-16).
    """
    version = _owned_version(session, owner.id, version_id)
    try:
        service.approve_version(
            session,
            version,
            approved_by=payload.approved_by,
            approval_source=payload.approval_source,
            approved_at=payload.approved_at,
            source_document_checksum=payload.source_document_checksum,
        )
    except service.PlanError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.REGIMEN_APPROVED,
        target_type="regimen_version",
        target_id=version.id,
        change_summary=f"approved_by={payload.approved_by}",
    )
    return _regimen_out(version)


@router.post(
    "/regimens/{version_id}/retire",
    response_model=RegimenVersionOut,
    dependencies=[Depends(require_csrf)],
)
def retire_regimen(version_id: uuid.UUID, session: DbSession, owner: CurrentOwner):
    version = _owned_version(session, owner.id, version_id)
    service.retire_version(session, version)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.REGIMEN_RETIRED,
        target_type="regimen_version",
        target_id=version.id,
    )
    return _regimen_out(version)


@router.get("/regimens/{version_id}/diff/{other_id}")
def diff_regimens(
    version_id: uuid.UUID, other_id: uuid.UUID, session: DbSession, owner: CurrentOwner
) -> dict[str, list[str]]:
    older = _owned_version(session, owner.id, version_id)
    newer = _owned_version(session, owner.id, other_id)
    return service.diff_versions(older, newer)


def _owned_version(
    session: DbSession, owner_id: uuid.UUID, version_id: uuid.UUID
) -> RegimenVersion:
    version = session.get(RegimenVersion, version_id)
    # 404 rather than 403 for someone else's row: a 403 would confirm it exists.
    if version is None or version.owner_id != owner_id:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="regimen version not found"
        )
    return version


def _regimen_out(v: RegimenVersion) -> RegimenVersionOut:
    return RegimenVersionOut(
        id=v.id,
        version_label=v.version_label,
        status=v.status,
        effective_from=v.effective_from,
        effective_to=v.effective_to,
        approved_at=v.approved_at,
        approved_by=v.approved_by,
        approval_source=v.approval_source,
        retired_at=v.retired_at,
        notes=v.notes,
        slots=[
            DoseSlotOut(
                id=s.id,
                medication_id=s.medication_id,
                medication_name=s.medication.name,
                scheduled_local_time=s.scheduled_local_time,
                amount=s.amount,
                unit=s.unit,
                route=s.route,
                condition=s.condition,
                sort_order=s.sort_order,
            )
            for s in sorted(v.slots, key=lambda s: s.scheduled_local_time)
        ],
        instructions=[
            InstructionOut(
                id=i.id,
                instruction_category=i.category,
                title=i.title,
                body=i.body,
                authored_by=i.authored_by,
                authored_on=i.authored_on,
                sort_order=i.sort_order,
            )
            for i in sorted(v.instructions, key=lambda i: i.sort_order)
        ],
    )
