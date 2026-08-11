"""Recorded doses and the plan-versus-actual comparison."""

from __future__ import annotations

import uuid
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.schemas import (
    DoseCorrectionIn,
    DoseIn,
    DoseOut,
    EventTimeOut,
    PlanComparisonDay,
    PlanComparisonRegimen,
    PlanComparisonSlot,
    ProvenanceOut,
)
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.timekeeping import (
    AmbiguousLocalTimeError,
    NonExistentLocalTimeError,
    UnknownTimezoneError,
)
from healthcurve.medications import service as meds
from healthcurve.medications.models import DoseEvent, Medication

router = APIRouter(tags=["doses"])


@router.post(
    "/doses",
    response_model=DoseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_dose(payload: DoseIn, session: DbSession, owner: CurrentOwner):
    medication = session.get(Medication, payload.medication_id)
    if medication is None or medication.owner_id != owner.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="medication not found")

    event_time = resolve_time(payload.time)

    # Record which plan was in force when this happened, so a later plan change cannot
    # retroactively make a past dose look wrong.
    version = meds.active_version_at(session, owner.id, event_time.occurred_at)

    dose = events.create_event(
        session,
        DoseEvent,
        owner_id=owner.id,
        event_time=event_time,
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        medication_id=payload.medication_id,
        amount=payload.amount,
        unit=payload.unit,
        route=payload.route,
        category=payload.category,
        regimen_version_id=version.id if version else None,
        slot_id=payload.slot_id,
        episode_id=payload.episode_id,
        notes=payload.notes,
    )
    return _dose_out(dose, medication)


@router.get("/doses", response_model=list[DoseOut])
def list_doses(
    session: DbSession,
    owner: CurrentOwner,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_superseded: bool = Query(
        default=False,
        description="Include corrected-away versions. Off by default so totals are correct.",
    ),
):
    query = select(DoseEvent).where(DoseEvent.owner_id == owner.id)
    if date_from is not None:
        query = query.where(DoseEvent.occurred_at >= date_from)
    if date_to is not None:
        query = query.where(DoseEvent.occurred_at <= date_to)

    rows = list(session.scalars(query.order_by(DoseEvent.occurred_at.desc())))
    if not include_superseded:
        rows = events.current_only(session, DoseEvent, rows)
    return [_dose_out(d, d.medication) for d in rows]


@router.post(
    "/doses/{dose_id}/correct",
    response_model=DoseOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def correct_dose(
    dose_id: uuid.UUID, payload: DoseCorrectionIn, session: DbSession, owner: CurrentOwner
):
    """Correct a dose. The original stays queryable with its original values (SAFE-08)."""
    original = session.get(DoseEvent, dose_id)
    if original is None or original.owner_id != owner.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="dose not found")

    changes = payload.changes.model_dump(exclude_unset=True, exclude={"time"})
    submitted_time = payload.changes.time if "time" in payload.changes.model_fields_set else None
    event_time = resolve_time(submitted_time) if submitted_time is not None else None
    if not changes and event_time is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="a correction must change at least one field",
        )

    if event_time is not None:
        version, slot = meds.association_for_event_time(
            session,
            owner_id=owner.id,
            medication_id=original.medication_id,
            occurred_at=event_time.occurred_at,
            local_time=event_time.local_time,
            timezone=event_time.timezone,
        )
        changes["regimen_version_id"] = version.id if version else None
        changes["slot_id"] = slot.id if slot else None

    try:
        correction = events.correct_event(
            session,
            DoseEvent,
            original,
            reason=payload.reason,
            changes=changes,
            event_time=event_time,
        )
    except events.CorrectionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc

    return _dose_out(correction, correction.medication)


@router.get("/doses/plan-comparison", response_model=PlanComparisonDay)
def plan_comparison(
    session: DbSession,
    owner: CurrentOwner,
    day: date = Query(description="Local calendar day to compare"),
    timezone: str | None = Query(default=None, description="IANA zone; defaults to owner setting"),
):
    """Compare a day's doses with the historical plan intervals in force that day.

    Missing slots are derived from the absence of a dose. No zero-dose row exists or is
    created (SAFE-10).
    """
    zone = timezone or owner.default_timezone
    try:
        result = meds.compare_day(session, owner_id=owner.id, day=day, timezone=zone)
    except Exception as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=f"invalid timezone: {zone}"
        ) from exc

    return PlanComparisonDay(
        date=result["date"],  # type: ignore[arg-type]
        timezone=result["timezone"],  # type: ignore[arg-type]
        regimen_version_id=result["regimen_version_id"],  # type: ignore[arg-type]
        regimen_version_label=result["regimen_version_label"],  # type: ignore[arg-type]
        regimen_versions=[
            PlanComparisonRegimen(
                id=version.id,
                version_label=version.version_label,
                effective_from=version.effective_from,
                effective_to=version.effective_to,
            )
            for version in result["regimen_versions"]  # type: ignore[union-attr]
        ],
        slots=[
            PlanComparisonSlot(
                slot_id=c.slot_id,
                medication_id=c.medication_id,
                medication_name=c.medication_name,
                scheduled_local_time=c.scheduled_local_time,  # type: ignore[arg-type]
                planned_amount=c.planned_amount,
                actual_amount=c.actual_amount,
                actual_local_time=c.actual_local_time,
                dose_id=c.dose_id,
                status=c.status,
                minutes_from_scheduled=c.minutes_from_scheduled,
                absolute_minutes_from_scheduled=c.absolute_minutes_from_scheduled,
                regimen_version_id=c.regimen_version_id,
                regimen_version_label=c.regimen_version_label,
                regimen_effective_from=c.regimen_effective_from,
                regimen_effective_to=c.regimen_effective_to,
                unit=c.unit,
                route=c.route,
            )
            for c in result["slots"]  # type: ignore[union-attr]
        ],
        planned_total=result["planned_total"],  # type: ignore[arg-type]
        actual_total=result["actual_total"],  # type: ignore[arg-type]
        unplanned_doses=result["unplanned_doses"],  # type: ignore[arg-type]
        missed_slots=result["missed_slots"],  # type: ignore[arg-type]
        metric_definition=result["metric_definition"],  # type: ignore[arg-type]
    )


def resolve_time(payload_time: object):
    """Turn a submitted local time into a resolved EventTime, or a 422."""
    try:
        return events.build_event_time(
            payload_time.local_time,  # type: ignore[attr-defined]
            payload_time.timezone,  # type: ignore[attr-defined]
            payload_time.fold,  # type: ignore[attr-defined]
        )
    except AmbiguousLocalTimeError as exc:
        # SAFE-13: surfaced to the user, never guessed.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except NonExistentLocalTimeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc
    except UnknownTimezoneError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)
        ) from exc


def _dose_out(dose: DoseEvent, medication: Medication) -> DoseOut:
    return DoseOut(
        id=dose.id,
        medication_id=dose.medication_id,
        medication_name=medication.name,
        amount=dose.amount,
        unit=dose.unit,
        route=dose.route,
        dose_category=dose.category,
        time=EventTimeOut(
            occurred_at=dose.occurred_at,
            local_time=dose.local_time,
            timezone=dose.timezone,
            utc_offset_minutes=dose.utc_offset_minutes,
        ),
        provenance=ProvenanceOut(
            recorded_at=dose.recorded_at,
            source_type=dose.source_type,
            confirmation_state=dose.confirmation_state,
            supersedes_id=dose.supersedes_id,
            correction_reason=dose.correction_reason,
            is_correction=dose.is_correction,
        ),
        regimen_version_id=dose.regimen_version_id,
        slot_id=dose.slot_id,
        episode_id=dose.episode_id,
        notes=dose.notes,
    )
