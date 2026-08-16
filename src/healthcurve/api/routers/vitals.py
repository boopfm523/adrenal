"""Manual blood-pressure, body-weight, and body-temperature facts."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.sql.elements import ColumnElement

from healthcurve.api.date_filters import local_date_window
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
    TemperatureCorrectionChanges,
    TemperatureCorrectionIn,
    TemperatureIn,
    TemperatureOut,
    TemperaturePage,
    WeightCorrectionChanges,
    WeightCorrectionIn,
    WeightIn,
    WeightOut,
    WeightPage,
)
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.vitals import service as vitals
from healthcurve.vitals.models import (
    BloodPressureEvent,
    TemperatureEvent,
    TemperatureUnit,
    WeightEvent,
    WeightUnit,
)

router = APIRouter(tags=["vitals"])
CorrectionChanges = (
    BloodPressureCorrectionChanges | WeightCorrectionChanges | TemperatureCorrectionChanges
)


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
        measurement_setting=payload.measurement_setting,
        body_position=payload.body_position,
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
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
):
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    predicates = _date_predicates(
        BloodPressureEvent,
        window.start or date_from,
        window.end_exclusive or date_to,
        end_exclusive=window.end_exclusive is not None,
    )
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
        measurement_setting=payload.measurement_setting,
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
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
):
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    predicates = _date_predicates(
        WeightEvent,
        window.start or date_from,
        window.end_exclusive or date_to,
        end_exclusive=window.end_exclusive is not None,
    )
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


@router.post(
    "/temperature",
    response_model=TemperatureOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_temperature(payload: TemperatureIn, session: DbSession, owner: CurrentOwner):
    _validate_temperature(payload.value, payload.unit)
    row = events.create_event(
        session,
        TemperatureEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        value=payload.value,
        unit=payload.unit,
        normalized_c=vitals.normalize_temperature_c(payload.value, payload.unit),
        notes=payload.notes,
    )
    return _temperature_out(row)


@router.get("/temperature", response_model=TemperaturePage)
def list_temperature(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
):
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    predicates = _date_predicates(
        TemperatureEvent,
        window.start or date_from,
        window.end_exclusive or date_to,
        end_exclusive=window.end_exclusive is not None,
    )
    page = paginate_current_facts(
        session,
        TemperatureEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=predicates,
    )
    return TemperaturePage(
        items=[_temperature_out(row) for row in page.items],
        revisions=[_temperature_out(row) for row in page.revisions],
        page=page.metadata,
    )


@router.post(
    "/temperature/{event_id}/correct",
    response_model=TemperatureOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def correct_temperature(
    event_id: uuid.UUID,
    payload: TemperatureCorrectionIn,
    session: DbSession,
    owner: CurrentOwner,
):
    original = _owned(session, TemperatureEvent, owner.id, event_id)
    changes = payload.changes.model_dump(exclude_unset=True, exclude={"time"})
    if "value" in changes or "unit" in changes:
        value = Decimal(changes.get("value", original.value))
        unit = TemperatureUnit(changes.get("unit", original.unit))
        _validate_temperature(value, unit)
        changes["normalized_c"] = vitals.normalize_temperature_c(value, unit)
    row = _correct(
        session,
        TemperatureEvent,
        original,
        payload.reason,
        payload.changes,
        prepared_changes=changes,
    )
    return _temperature_out(row)


def _date_predicates[VitalEvent: (BloodPressureEvent, WeightEvent, TemperatureEvent)](
    model: type[VitalEvent],
    start: datetime | None,
    end: datetime | None,
    *,
    end_exclusive: bool = False,
) -> tuple[ColumnElement[bool], ...]:
    predicates: list[ColumnElement[bool]] = []
    if start is not None:
        predicates.append(model.occurred_at >= start)
    if end is not None:
        predicates.append(model.occurred_at < end if end_exclusive else model.occurred_at <= end)
    return tuple(predicates)


def _owned[VitalEvent: (BloodPressureEvent, WeightEvent, TemperatureEvent)](
    session: DbSession,
    model: type[VitalEvent],
    owner_id: uuid.UUID,
    event_id: uuid.UUID,
) -> VitalEvent:
    row = session.get(model, event_id)
    if row is None or row.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return row


def _correct[VitalEvent: (BloodPressureEvent, WeightEvent, TemperatureEvent)](
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
        measurement_setting=row.measurement_setting,
        body_position=row.body_position,
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
        measurement_setting=row.measurement_setting,
        time=time_out(row),
        provenance=provenance_out(row),
        notes=row.notes,
    )


def _temperature_out(row: TemperatureEvent) -> TemperatureOut:
    return TemperatureOut(
        id=row.id,
        value=row.value,
        unit=row.unit,
        normalized_c=row.normalized_c,
        display_f=vitals.display_temperature_f(row.value, row.unit),
        display_c=vitals.display_temperature_c(row.value, row.unit),
        time=time_out(row),
        provenance=provenance_out(row),
        notes=row.notes,
    )


def _validate_temperature(value: Decimal, unit: TemperatureUnit) -> None:
    if not vitals.temperature_in_range(value, unit):
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="temperature must be between 25 and 45 °C (77 and 113 °F)",
        )
