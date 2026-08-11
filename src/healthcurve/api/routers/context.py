"""Owner-controlled location, timezone, and weather context API."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import Field, model_validator
from sqlalchemy import select

from healthcurve.api.date_filters import local_date_window
from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.pagination import Pagination, paginate_current_facts
from healthcurve.api.routers.events import provenance_out, resolve_time, time_out
from healthcurve.api.schemas import (
    ApiModel,
    EventTimeIn,
    EventTimeOut,
    FactResource,
    PageMetadata,
    ProvenanceOut,
)
from healthcurve.context.models import (
    ContextEvent,
    LocationPrecision,
    PrecipitationUnit,
    PressureUnit,
    TemperatureUnit,
)
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.identity import service as auth
from healthcurve.operations import audit

router = APIRouter(prefix="/context-events", tags=["context"])


class ContextFields(ApiModel):
    location_precision: LocationPrecision = LocationPrecision.NONE
    coarse_location_label: str | None = Field(default=None, min_length=1, max_length=120)
    latitude: Decimal | None = Field(default=None, ge=-90, le=90, max_digits=9, decimal_places=6)
    longitude: Decimal | None = Field(default=None, ge=-180, le=180, max_digits=9, decimal_places=6)
    exact_location_consent: bool = False
    weather_provider: str | None = Field(default=None, min_length=1, max_length=64)
    weather_observation_id: str | None = Field(default=None, max_length=255)
    weather_observed_at: datetime | None = None
    temperature: Decimal | None = Field(default=None, max_digits=6, decimal_places=2)
    temperature_unit: TemperatureUnit | None = None
    pressure: Decimal | None = Field(default=None, gt=0, max_digits=8, decimal_places=2)
    pressure_unit: PressureUnit | None = None
    humidity_percent: Decimal | None = Field(
        default=None, ge=0, le=100, max_digits=5, decimal_places=2
    )
    precipitation: Decimal | None = Field(default=None, ge=0, max_digits=8, decimal_places=2)
    precipitation_unit: PrecipitationUnit | None = None
    conditions: str | None = Field(default=None, max_length=200)
    weather_confidence: Decimal | None = Field(
        default=None, ge=0, le=1, max_digits=4, decimal_places=3
    )
    notes: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def validate_privacy_and_provenance(self) -> ContextFields:
        coordinates = self.latitude is not None or self.longitude is not None
        if (self.latitude is None) != (self.longitude is None):
            raise ValueError("latitude and longitude must be supplied together")
        if self.location_precision is LocationPrecision.NONE and (
            self.coarse_location_label is not None or coordinates or self.exact_location_consent
        ):
            raise ValueError("location fields require coarse or exact precision")
        if self.location_precision is LocationPrecision.COARSE:
            if self.coarse_location_label is None or self.exact_location_consent:
                raise ValueError("coarse location requires a label and forbids exact consent")
            if (
                self.latitude is not None
                and self.longitude is not None
                and (
                    self.latitude != self.latitude.quantize(Decimal("0.1"))
                    or self.longitude != self.longitude.quantize(Decimal("0.1"))
                )
            ):
                raise ValueError("coarse coordinates must be rounded to 0.1 degrees")
        if self.location_precision is LocationPrecision.EXACT and (
            not coordinates or not self.exact_location_consent
        ):
            raise ValueError("exact coordinates require explicit consent")
        for value, unit, name in (
            (self.temperature, self.temperature_unit, "temperature"),
            (self.pressure, self.pressure_unit, "pressure"),
            (self.precipitation, self.precipitation_unit, "precipitation"),
        ):
            if (value is None) != (unit is None):
                raise ValueError(f"{name} value and unit must be supplied together")
        weather_values = (
            self.temperature,
            self.pressure,
            self.humidity_percent,
            self.precipitation,
            self.conditions,
            self.weather_observation_id,
            self.weather_confidence,
        )
        has_weather = self.weather_provider is not None or any(
            value is not None for value in weather_values
        )
        if has_weather and (self.weather_provider is None or self.weather_observed_at is None):
            raise ValueError("weather values require provider and observation time")
        if self.weather_observed_at is not None and self.weather_observed_at.utcoffset() is None:
            raise ValueError("weather observation time must include a UTC offset")
        if self.weather_provider is not None and self.weather_provider != "manual":
            raise ValueError("only manual weather provenance is enabled")
        return self


class ContextIn(ContextFields):
    time: EventTimeIn


class ContextCorrectionIn(ApiModel):
    reason: str = Field(min_length=1, max_length=500)
    replacement: ContextIn


class ContextDeleteIn(ApiModel):
    password: str = Field(min_length=1, max_length=512)


class ContextOut(FactResource, ContextFields):
    id: uuid.UUID
    time: EventTimeOut
    provenance: ProvenanceOut


class ContextPage(ApiModel):
    items: list[ContextOut]
    revisions: list[ContextOut]
    page: PageMetadata


def _fields(payload: ContextFields) -> dict[str, Any]:
    return payload.model_dump(exclude={"time"})


def _out(row: ContextEvent) -> ContextOut:
    return ContextOut(
        id=row.id,
        time=time_out(row),
        provenance=provenance_out(row),
        **{name: getattr(row, name) for name in ContextFields.model_fields},
    )


def _owned(session: DbSession, owner_id: uuid.UUID, event_id: uuid.UUID) -> ContextEvent:
    row = session.get(ContextEvent, event_id)
    if row is None or row.owner_id != owner_id:
        raise HTTPException(status_code=404, detail="context record not found")
    return row


@router.post(
    "",
    response_model=ContextOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_context(payload: ContextIn, session: DbSession, owner: CurrentOwner) -> ContextOut:
    row = events.create_event(
        session,
        ContextEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        **_fields(payload),
    )
    return _out(row)


@router.get("", response_model=ContextPage)
def list_context(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
) -> ContextPage:
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    predicates = []
    if window.start is not None:
        predicates.append(ContextEvent.occurred_at >= window.start)
    if window.end_exclusive is not None:
        predicates.append(ContextEvent.occurred_at < window.end_exclusive)
    page = paginate_current_facts(
        session,
        ContextEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=tuple(predicates),
    )
    return ContextPage(
        items=[_out(row) for row in page.items],
        revisions=[_out(row) for row in page.revisions],
        page=page.metadata,
    )


@router.post(
    "/{event_id}/correct",
    response_model=ContextOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def correct_context(
    event_id: uuid.UUID,
    payload: ContextCorrectionIn,
    session: DbSession,
    owner: CurrentOwner,
) -> ContextOut:
    original = _owned(session, owner.id, event_id)
    try:
        row = events.correct_event(
            session,
            ContextEvent,
            original,
            reason=payload.reason,
            changes=_fields(payload.replacement),
            event_time=resolve_time(payload.replacement.time),
        )
    except events.CorrectionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return _out(row)


@router.delete(
    "/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_context(
    event_id: uuid.UUID,
    payload: ContextDeleteIn,
    session: DbSession,
    owner: CurrentOwner,
) -> None:
    selected = _owned(session, owner.id, event_id)
    if not auth.verify_password(owner.password_hash, payload.password):
        raise HTTPException(status_code=403, detail="current password is incorrect")
    rows = list(session.scalars(select(ContextEvent).where(ContextEvent.owner_id == owner.id)))
    by_id = {row.id: row for row in rows}
    root = selected
    while root.supersedes_id is not None:
        parent = by_id.get(root.supersedes_id)
        if parent is None:
            raise HTTPException(status_code=409, detail="context correction chain is incomplete")
        root = parent
    chain = [root]
    while True:
        child = next((row for row in rows if row.supersedes_id == chain[-1].id), None)
        if child is None:
            break
        chain.append(child)
    for row in reversed(chain):
        session.delete(row)
        # The self-FK is RESTRICT so correction history cannot be orphaned. Flush each
        # child before its parent; otherwise SQLAlchemy may batch the deletes in UUID
        # order and PostgreSQL correctly rejects a parent-first batch.
        session.flush()
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.RECORD_DELETED,
        target_type="context_event",
        target_id=root.id,
        change_summary=f"deleted context correction chain ({len(chain)} revisions)",
    )
