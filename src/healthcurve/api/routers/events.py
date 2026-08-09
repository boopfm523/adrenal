"""Symptoms, diary entries, life events, and the unified timeline."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import select

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.routers.doses import resolve_time
from healthcurve.api.schemas import (
    DiaryIn,
    DiaryOut,
    EventTimeOut,
    LifeEventIn,
    LifeEventOut,
    ProvenanceOut,
    SymptomCorrectionIn,
    SymptomIn,
    SymptomOut,
    TimelineItem,
    TimelinePage,
)
from healthcurve.episodes.models import EmergencyInjectionEvent
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, EventMixin, SourceType
from healthcurve.events.models import DiaryEvent, LifeEvent, SymptomEvent
from healthcurve.medications.models import DoseEvent

router = APIRouter(tags=["events"])


def time_out(e: EventMixin) -> EventTimeOut:
    return EventTimeOut(
        occurred_at=e.occurred_at,
        local_time=e.local_time,
        timezone=e.timezone,
        utc_offset_minutes=e.utc_offset_minutes,
    )


def provenance_out(e: EventMixin) -> ProvenanceOut:
    return ProvenanceOut(
        recorded_at=e.recorded_at,
        source_type=e.source_type,
        confirmation_state=e.confirmation_state,
        supersedes_id=e.supersedes_id,
        correction_reason=e.correction_reason,
        is_correction=e.is_correction,
    )


# ---------------------------------------------------------------------------
# Symptoms
# ---------------------------------------------------------------------------


@router.post(
    "/symptoms",
    response_model=SymptomOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_symptom(payload: SymptomIn, session: DbSession, owner: CurrentOwner):
    event = events.create_event(
        session,
        SymptomEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        name=payload.name,
        severity=payload.severity,
        body_area=payload.body_area,
        ended_at=payload.ended_at,
        episode_id=payload.episode_id,
        notes=payload.notes,
    )
    return _symptom_out(event)


@router.get("/symptoms", response_model=list[SymptomOut])
def list_symptoms(
    session: DbSession,
    owner: CurrentOwner,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    include_superseded: bool = False,
):
    query = select(SymptomEvent).where(SymptomEvent.owner_id == owner.id)
    if date_from:
        query = query.where(SymptomEvent.occurred_at >= date_from)
    if date_to:
        query = query.where(SymptomEvent.occurred_at <= date_to)
    rows = list(session.scalars(query.order_by(SymptomEvent.occurred_at.desc())))
    if not include_superseded:
        rows = events.current_only(session, SymptomEvent, rows)
    return [_symptom_out(e) for e in rows]


@router.post(
    "/symptoms/{event_id}/correct",
    response_model=SymptomOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def correct_symptom(
    event_id: uuid.UUID, payload: SymptomCorrectionIn, session: DbSession, owner: CurrentOwner
):
    original = _owned_symptom(session, owner.id, event_id)
    changes = payload.changes.model_dump(exclude_unset=True, exclude={"time"})
    submitted_time = payload.changes.time if "time" in payload.changes.model_fields_set else None
    event_time = resolve_time(submitted_time) if submitted_time is not None else None
    if not changes and event_time is None:
        raise HTTPException(status_code=422, detail="a correction must change at least one field")
    try:
        correction = events.correct_event(
            session,
            SymptomEvent,
            original,
            reason=payload.reason,
            changes=changes,
            event_time=event_time,
        )
    except events.CorrectionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _symptom_out(correction)


def _symptom_out(e: SymptomEvent) -> SymptomOut:
    return SymptomOut(
        id=e.id,
        name=e.name,
        severity=e.severity,
        body_area=e.body_area,
        time=time_out(e),
        provenance=provenance_out(e),
        episode_id=e.episode_id,
        notes=e.notes,
    )


# ---------------------------------------------------------------------------
# Diary
# ---------------------------------------------------------------------------


@router.post(
    "/diary-events",
    response_model=DiaryOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_diary(payload: DiaryIn, session: DbSession, owner: CurrentOwner):
    event = events.create_event(
        session,
        DiaryEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        text=payload.text,
        is_sensitive=payload.is_sensitive,
        tags=payload.tags,
    )
    return _diary_out(event)


@router.get("/diary-events", response_model=list[DiaryOut])
def list_diary(
    session: DbSession,
    owner: CurrentOwner,
    include_sensitive: bool = Query(
        default=False, description="Sensitive entries are excluded from default views (T7)."
    ),
):
    query = select(DiaryEvent).where(DiaryEvent.owner_id == owner.id)
    if not include_sensitive:
        query = query.where(DiaryEvent.is_sensitive.is_(False))
    rows = list(session.scalars(query.order_by(DiaryEvent.occurred_at.desc())))
    return [_diary_out(e) for e in events.current_only(session, DiaryEvent, rows)]


def _diary_out(e: DiaryEvent) -> DiaryOut:
    return DiaryOut(
        id=e.id,
        text=e.text,
        is_sensitive=e.is_sensitive,
        tags=e.tags,
        time=time_out(e),
        provenance=provenance_out(e),
    )


# ---------------------------------------------------------------------------
# Life events
# ---------------------------------------------------------------------------


@router.post(
    "/life-events",
    response_model=LifeEventOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_life_event(payload: LifeEventIn, session: DbSession, owner: CurrentOwner):
    event = events.create_event(
        session,
        LifeEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        title=payload.title,
        category=payload.category,
        description=payload.description,
        ended_at=payload.ended_at,
        is_sensitive=payload.is_sensitive,
    )
    return _life_out(event)


@router.get("/life-events", response_model=list[LifeEventOut])
def list_life_events(session: DbSession, owner: CurrentOwner, include_sensitive: bool = False):
    query = select(LifeEvent).where(LifeEvent.owner_id == owner.id)
    if not include_sensitive:
        query = query.where(LifeEvent.is_sensitive.is_(False))
    rows = list(session.scalars(query.order_by(LifeEvent.occurred_at.desc())))
    return [_life_out(e) for e in events.current_only(session, LifeEvent, rows)]


def _life_out(e: LifeEvent) -> LifeEventOut:
    return LifeEventOut(
        id=e.id,
        title=e.title,
        life_category=e.category,
        description=e.description,
        is_sensitive=e.is_sensitive,
        time=time_out(e),
        provenance=provenance_out(e),
    )


# ---------------------------------------------------------------------------
# Timeline
# ---------------------------------------------------------------------------

#: Event types the timeline can merge. Each supplies a summary that carries no more
#: detail than the list view needs.
_TIMELINE_TYPES: tuple[tuple[type[EventMixin], str], ...] = (
    (DoseEvent, "dose"),
    (SymptomEvent, "symptom"),
    (DiaryEvent, "diary"),
    (LifeEvent, "life_event"),
    (EmergencyInjectionEvent, "emergency_injection"),
)


@router.get("/timeline", response_model=TimelinePage)
def timeline(
    session: DbSession,
    owner: CurrentOwner,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    types: str | None = Query(default=None, description="Comma-separated event types"),
    timezone: str | None = None,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    include_sensitive: bool = False,
    limit: int = Query(default=200, ge=1, le=1000),
):
    """The unified timeline.

    Every item carries its own category, source, timezone, and correction state, so a
    reader can tell a confirmed manual entry from a provider import at a glance
    (SAFE-02, plan section 10).
    """
    zone_name = timezone or owner.default_timezone
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(status_code=422, detail="invalid timezone") from exc
    if local_date_from is not None:
        date_from = datetime.combine(local_date_from, time.min, tzinfo=zone)
    if local_date_to is not None:
        date_to = datetime.combine(local_date_to + timedelta(days=1), time.min, tzinfo=zone)
    date_to_is_exclusive = local_date_to is not None

    wanted = {t.strip() for t in types.split(",")} if types else None
    items: list[TimelineItem] = []

    for model, type_name in _TIMELINE_TYPES:
        if wanted is not None and type_name not in wanted:
            continue

        query = select(model).where(model.owner_id == owner.id)
        if date_from:
            query = query.where(model.occurred_at >= date_from)
        if date_to:
            query = query.where(
                model.occurred_at < date_to
                if date_to_is_exclusive
                else model.occurred_at <= date_to
            )

        rows = list(session.scalars(query.order_by(model.occurred_at.desc()).limit(limit)))
        for row in events.current_only(session, model, rows):
            sensitive = bool(getattr(row, "is_sensitive", False))
            if sensitive and not include_sensitive:
                continue
            items.append(
                TimelineItem(
                    id=row.id,
                    category="fact",
                    event_type=type_name,
                    summary=_summarize(row, type_name),
                    time=time_out(row),
                    provenance=provenance_out(row),
                    is_sensitive=sensitive,
                )
            )

    items.sort(key=lambda i: i.time.occurred_at, reverse=True)
    return TimelinePage(
        items=items[:limit],
        timezone=zone_name,
        next_cursor=None,
    )


def _summarize(row: EventMixin, type_name: str) -> str:
    match type_name:
        case "dose":
            return f"{row.medication.name} {row.amount} {row.unit} ({row.category})"  # type: ignore[attr-defined]
        case "symptom":
            severity = f" severity {row.severity}/10" if row.severity is not None else ""  # type: ignore[attr-defined]
            return f"{row.name}{severity}"  # type: ignore[attr-defined]
        case "diary":
            text = row.text  # type: ignore[attr-defined]
            return text[:120] + ("..." if len(text) > 120 else "")
        case "life_event":
            return f"{row.title} ({row.category})"  # type: ignore[attr-defined]
        case "emergency_injection":
            return f"Emergency injection {row.amount} {row.unit}"  # type: ignore[attr-defined]
        case _:  # pragma: no cover
            return type_name


def _owned_symptom(session: DbSession, owner_id: uuid.UUID, event_id: uuid.UUID) -> SymptomEvent:
    row = session.get(SymptomEvent, event_id)
    if row is None or row.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return row
