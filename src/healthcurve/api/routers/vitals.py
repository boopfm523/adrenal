"""Manual blood-pressure and body-weight facts."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.sql.elements import ColumnElement

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.pagination import Pagination, paginate_current_facts
from healthcurve.api.routers.doses import resolve_time
from healthcurve.api.routers.events import provenance_out, time_out
from healthcurve.api.schemas import (
    BloodPressureCorrectionChanges,
    BloodPressureCorrectionIn,
    BloodPressureIn,
    BloodPressureOut,
    BloodPressurePage,
    WeightCorrectionChanges,
    WeightCorrectionIn,
    WeightIn,
    WeightOut,
    WeightPage,
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


@router.get("/blood-pressure", response_model=BloodPressurePage)
def list_blood_pressure(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    predicates = _date_predicates(BloodPressureEvent, date_from, date_to)
    page = paginate_current_facts(
        session,
        BloodPressureEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=predicates,
    )
    return BloodPressurePage(
        items=[_blood_pressure_out(row) for row in page.items],
        revisions=[_blood_pressure_out(row) for row in page.revisions],
        page=page.metadata,
    )


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


@router.get("/weight", response_model=WeightPage)
def list_weight(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
):
    predicates = _date_predicates(WeightEvent, date_from, date_to)
    page = paginate_current_facts(
        session,
        WeightEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=predicates,
    )
    return WeightPage(
        items=[_weight_out(row) for row in page.items],
        revisions=[_weight_out(row) for row in page.revisions],
        page=page.metadata,
    )


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


def _date_predicates[VitalEvent: (BloodPressureEvent, WeightEvent)](
    model: type[VitalEvent],
    start: datetime | None,
    end: datetime | None,
) -> tuple[ColumnElement[bool], ...]:
    predicates: list[ColumnElement[bool]] = []
    if start is not None:
        predicates.append(model.occurred_at >= start)
    if end is not None:
        predicates.append(model.occurred_at <= end)
    return tuple(predicates)


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
        display_lb=vitals.display_weight_lb(row.value, row.unit),
        time=time_out(row),
        provenance=provenance_out(row),
        notes=row.notes,
    )
