"""Manual blood-pressure and body-weight facts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import select

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.routers.doses import resolve_time
from healthcurve.api.routers.events import provenance_out, time_out
from healthcurve.api.schemas import (
    BloodPressureCorrectionChanges,
    BloodPressureCorrectionIn,
    BloodPressureIn,
    BloodPressureOut,
    WeightCorrectionChanges,
    WeightCorrectionIn,
    WeightIn,
    WeightOut,
)
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.vitals import service as vitals
from healthcurve.vitals.models import BloodPressureEvent, WeightEvent, WeightUnit

router = APIRouter(tags=["vitals"])
CorrectionChanges = BloodPressureCorrectionChanges | WeightCorrectionChanges


@router.post(
    "/blood-pressure",
    response_model=BloodPressureOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_blood_pressure(payload: BloodPressureIn, session: DbSession, owner: CurrentOwner):
    row = events.create_event(
        session,
        BloodPressureEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        systolic_mmhg=payload.systolic_mmhg,
        diastolic_mmhg=payload.diastolic_mmhg,
        pulse_bpm=payload.pulse_bpm,
        notes=payload.notes,
    )
    return _blood_pressure_out(row)


@router.get("/blood-pressure", response_model=list[BloodPressureOut])
def list_blood_pressure(
    session: DbSession,
    owner: CurrentOwner,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_superseded: bool = False,
):
    rows = _list_rows(session, BloodPressureEvent, owner.id, date_from, date_to)
    if not include_superseded:
        rows = events.current_only(session, BloodPressureEvent, rows)
    return [_blood_pressure_out(row) for row in rows]


@router.post(
    "/blood-pressure/{event_id}/correct",
    response_model=BloodPressureOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def correct_blood_pressure(
    event_id: uuid.UUID,
    payload: BloodPressureCorrectionIn,
    session: DbSession,
    owner: CurrentOwner,
):
    original = _owned(session, BloodPressureEvent, owner.id, event_id)
    row = _correct(session, BloodPressureEvent, original, payload.reason, payload.changes)
    return _blood_pressure_out(row)


@router.post(
    "/weight",
    response_model=WeightOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_weight(payload: WeightIn, session: DbSession, owner: CurrentOwner):
    row = events.create_event(
        session,
        WeightEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        value=payload.value,
        unit=payload.unit,
        normalized_kg=vitals.normalize_weight_kg(payload.value, payload.unit),
        notes=payload.notes,
    )
    return _weight_out(row)


@router.get("/weight", response_model=list[WeightOut])
def list_weight(
    session: DbSession,
    owner: CurrentOwner,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_superseded: bool = False,
):
    rows = _list_rows(session, WeightEvent, owner.id, date_from, date_to)
    if not include_superseded:
        rows = events.current_only(session, WeightEvent, rows)
    return [_weight_out(row) for row in rows]


@router.post(
    "/weight/{event_id}/correct",
    response_model=WeightOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def correct_weight(
    event_id: uuid.UUID,
    payload: WeightCorrectionIn,
    session: DbSession,
    owner: CurrentOwner,
):
    original = _owned(session, WeightEvent, owner.id, event_id)
    changes = payload.changes.model_dump(exclude_unset=True, exclude={"time"})
    if "value" in changes or "unit" in changes:
        value = Decimal(changes.get("value", original.value))
        unit = WeightUnit(changes.get("unit", original.unit))
        changes["normalized_kg"] = vitals.normalize_weight_kg(value, unit)
    row = _correct(
        session,
        WeightEvent,
        original,
        payload.reason,
        payload.changes,
        prepared_changes=changes,
    )
    return _weight_out(row)


def _list_rows[VitalEvent: (BloodPressureEvent, WeightEvent)](
    session: DbSession,
    model: type[VitalEvent],
    owner_id: uuid.UUID,
    start: datetime | None,
    end: datetime | None,
) -> list[VitalEvent]:
    query = select(model).where(model.owner_id == owner_id)
    if start is not None:
        query = query.where(model.occurred_at >= start)
    if end is not None:
        query = query.where(model.occurred_at <= end)
    return list(session.scalars(query.order_by(model.occurred_at.desc())))


def _owned[VitalEvent: (BloodPressureEvent, WeightEvent)](
    session: DbSession,
    model: type[VitalEvent],
    owner_id: uuid.UUID,
    event_id: uuid.UUID,
) -> VitalEvent:
    row = session.get(model, event_id)
    if row is None or row.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return row


def _correct[VitalEvent: (BloodPressureEvent, WeightEvent)](
    session: DbSession,
    model: type[VitalEvent],
    original: VitalEvent,
    reason: str,
    payload: CorrectionChanges,
    *,
    prepared_changes: dict[str, Any] | None = None,
) -> VitalEvent:
    changes = (
        prepared_changes
        if prepared_changes is not None
        else payload.model_dump(exclude_unset=True, exclude={"time"})
    )
    submitted_time = payload.time if "time" in payload.model_fields_set else None
    event_time = resolve_time(submitted_time) if submitted_time is not None else None
    if not changes and event_time is None:
        raise HTTPException(status_code=422, detail="a correction must change at least one field")
    try:
        return events.correct_event(
            session, model, original, reason=reason, changes=changes, event_time=event_time
        )
    except events.CorrectionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc


def _blood_pressure_out(row: BloodPressureEvent) -> BloodPressureOut:
    return BloodPressureOut(
        id=row.id,
        systolic_mmhg=row.systolic_mmhg,
        diastolic_mmhg=row.diastolic_mmhg,
        pulse_bpm=row.pulse_bpm,
        time=time_out(row),
        provenance=provenance_out(row),
        notes=row.notes,
    )


def _weight_out(row: WeightEvent) -> WeightOut:
    return WeightOut(
        id=row.id,
        value=row.value,
        unit=row.unit,
        normalized_kg=row.normalized_kg,
        time=time_out(row),
        provenance=provenance_out(row),
        notes=row.notes,
    )
