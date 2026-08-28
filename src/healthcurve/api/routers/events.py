"""Symptoms, diary entries, life events, and the unified timeline."""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timedelta
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy import func, literal, select, union_all
from sqlalchemy.sql.elements import ColumnElement

from healthcurve.ai.models import AIAnalysis
from healthcurve.api.date_filters import local_date_window
from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.pagination import Pagination, page_metadata, paginate_current_facts
from healthcurve.api.routers.doses import resolve_time
from healthcurve.api.schemas import (
    DiaryIn,
    DiaryOut,
    DiaryPage,
    EventTimeOut,
    LifeEventIn,
    LifeEventOut,
    LifeEventPage,
    MealCorrectionIn,
    MealIn,
    MealOut,
    MealPage,
    ProvenanceOut,
    SymptomCorrectionIn,
    SymptomIn,
    SymptomOut,
    SymptomPage,
    TimelineItem,
    TimelinePage,
)
from healthcurve.context.models import ContextEvent, LocationPrecision
from healthcurve.episodes.models import EmergencyInjectionEvent
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, EventMixin, SourceType
from healthcurve.events.models import (
    SYMPTOM_TRACKING_CATEGORY_REVISION,
    DiaryEvent,
    LifeEvent,
    MealEvent,
    SymptomEvent,
)
from healthcurve.events.timekeeping import timezone_abbreviation
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminMetricEvent,
    GarminSleepEvent,
)
from healthcurve.integrations.garmin.presentation import measurement_summary
from healthcurve.medications.models import DoseEvent
from healthcurve.vitals import service as vitals
from healthcurve.vitals.models import (
    BloodPressureEvent,
    TemperatureEvent,
    WeightEvent,
    WeightUnit,
)

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
        tracking_category=payload.tracking_category,
        tracking_category_revision=(
            SYMPTOM_TRACKING_CATEGORY_REVISION if payload.tracking_category is not None else None
        ),
        ended_at=payload.ended_at,
        episode_id=payload.episode_id,
        notes=payload.notes,
    )
    return _symptom_out(event)


@router.get("/symptoms", response_model=SymptomPage)
def list_symptoms(
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
    predicates: list[ColumnElement[bool]] = []
    if window.start is not None or date_from is not None:
        predicates.append(SymptomEvent.occurred_at >= (window.start or date_from))
    if window.end_exclusive is not None:
        predicates.append(SymptomEvent.occurred_at < window.end_exclusive)
    elif date_to is not None:
        predicates.append(SymptomEvent.occurred_at <= date_to)
    page = paginate_current_facts(
        session,
        SymptomEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=tuple(predicates),
    )
    return SymptomPage(
        items=[_symptom_out(row) for row in page.items],
        revisions=[_symptom_out(row) for row in page.revisions],
        page=page.metadata,
    )


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
    if "tracking_category" in payload.changes.model_fields_set:
        changes["tracking_category_revision"] = (
            SYMPTOM_TRACKING_CATEGORY_REVISION
            if payload.changes.tracking_category is not None
            else None
        )
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


@router.delete(
    "/symptoms/{event_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_symptom(event_id: uuid.UUID, session: DbSession, owner: CurrentOwner) -> None:
    selected = _owned_symptom(session, owner.id, event_id)
    try:
        _, deleted_ids = events.delete_correction_chain(session, SymptomEvent, selected)
    except events.DeletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc

    deleted_keys = {str(value) for value in deleted_ids}
    active_analyses = session.scalars(
        select(AIAnalysis).where(
            AIAnalysis.owner_id == owner.id,
            AIAnalysis.hidden_at.is_(None),
        )
    )
    invalidated_at = datetime.now(tz=ZoneInfo("UTC"))
    for analysis in active_analyses:
        if deleted_keys.intersection(analysis.source_record_ids):
            analysis.hidden_at = invalidated_at


def _symptom_out(e: SymptomEvent) -> SymptomOut:
    return SymptomOut(
        id=e.id,
        name=e.name,
        severity=e.severity,
        body_area=e.body_area,
        tracking_category=e.tracking_category,
        tracking_category_revision=e.tracking_category_revision,
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


@router.get("/diary-events", response_model=DiaryPage)
def list_diary(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    include_sensitive: bool = Query(
        default=False, description="Sensitive entries are excluded from default views (T7)."
    ),
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
) -> DiaryPage:
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    predicates: list[ColumnElement[bool]] = (
        [] if include_sensitive else [DiaryEvent.is_sensitive.is_(False)]
    )
    if window.start is not None:
        predicates.append(DiaryEvent.occurred_at >= window.start)
    if window.end_exclusive is not None:
        predicates.append(DiaryEvent.occurred_at < window.end_exclusive)
    page = paginate_current_facts(
        session,
        DiaryEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=tuple(predicates),
        include_revisions=False,
    )
    return DiaryPage(
        items=[_diary_out(row) for row in page.items],
        revisions=[_diary_out(row) for row in page.revisions],
        page=page.metadata,
    )


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
# Meals
# ---------------------------------------------------------------------------


@router.post(
    "/meal-events",
    response_model=MealOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_meal(payload: MealIn, session: DbSession, owner: CurrentOwner) -> MealOut:
    event = events.create_event(
        session,
        MealEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        size=payload.size,
        notes=payload.notes,
    )
    return _meal_out(event)


@router.get("/meal-events", response_model=MealPage)
def list_meals(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
) -> MealPage:
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    predicates: list[ColumnElement[bool]] = []
    if window.start is not None:
        predicates.append(MealEvent.occurred_at >= window.start)
    if window.end_exclusive is not None:
        predicates.append(MealEvent.occurred_at < window.end_exclusive)
    page = paginate_current_facts(
        session,
        MealEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=tuple(predicates),
    )
    return MealPage(
        items=[_meal_out(row) for row in page.items],
        revisions=[_meal_out(row) for row in page.revisions],
        page=page.metadata,
    )


@router.post(
    "/meal-events/{event_id}/correct",
    response_model=MealOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def correct_meal(
    event_id: uuid.UUID,
    payload: MealCorrectionIn,
    session: DbSession,
    owner: CurrentOwner,
) -> MealOut:
    original = _owned_meal(session, owner.id, event_id)
    changes = payload.changes.model_dump(exclude_unset=True, exclude={"time"})
    submitted_time = payload.changes.time if "time" in payload.changes.model_fields_set else None
    event_time = resolve_time(submitted_time) if submitted_time is not None else None
    if not changes and event_time is None:
        raise HTTPException(status_code=422, detail="a correction must change at least one field")
    try:
        correction = events.correct_event(
            session,
            MealEvent,
            original,
            reason=payload.reason,
            changes=changes,
            event_time=event_time,
        )
    except events.CorrectionError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _meal_out(correction)


def _meal_out(e: MealEvent) -> MealOut:
    return MealOut(
        id=e.id,
        size=e.size,
        time=time_out(e),
        provenance=provenance_out(e),
        notes=e.notes,
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


@router.get("/life-events", response_model=LifeEventPage)
def list_life_events(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    include_sensitive: bool = False,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    timezone: str | None = None,
) -> LifeEventPage:
    window = local_date_window(
        profile_timezone=owner.default_timezone,
        timezone=timezone,
        date_from=local_date_from,
        date_to=local_date_to,
    )
    predicates: list[ColumnElement[bool]] = (
        [] if include_sensitive else [LifeEvent.is_sensitive.is_(False)]
    )
    if window.start is not None:
        predicates.append(LifeEvent.occurred_at >= window.start)
    if window.end_exclusive is not None:
        predicates.append(LifeEvent.occurred_at < window.end_exclusive)
    page = paginate_current_facts(
        session,
        LifeEvent,
        owner_id=owner.id,
        request=pagination,
        predicates=tuple(predicates),
        include_revisions=False,
    )
    return LifeEventPage(
        items=[_life_out(row) for row in page.items],
        revisions=[_life_out(row) for row in page.revisions],
        page=page.metadata,
    )


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
    (MealEvent, "meal"),
    (LifeEvent, "life_event"),
    (EmergencyInjectionEvent, "emergency_injection"),
    (ContextEvent, "context"),
    (BloodPressureEvent, "blood_pressure"),
    (WeightEvent, "weight"),
    (TemperatureEvent, "temperature"),
    (GarminMetricEvent, "garmin_daily"),
    (GarminSleepEvent, "garmin_sleep"),
    (GarminActivityEvent, "garmin_activity"),
)


@router.get("/timeline", response_model=TimelinePage)
def timeline(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    date_from: datetime | None = None,
    date_to: datetime | None = None,
    types: str | None = Query(default=None, description="Comma-separated event types"),
    timezone: str | None = None,
    local_date_from: date | None = None,
    local_date_to: date | None = None,
    include_sensitive: bool = False,
    sort_order: Literal["asc", "desc"] = Query(
        default="desc",
        description="Order by experienced event time: asc is earliest first, desc is latest first",
    ),
):
    """The unified timeline.

    Every item carries its own category, source, timezone, and correction state, so a
    reader can tell a confirmed manual entry from a provider import at a glance
    (SAFE-02, plan section 10). Ordering always uses the experienced instant
    (``occurred_at``), never insertion or recording time. Equal instants are ordered
    deterministically by event type and stable record id.
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
    source_queries = []

    for model, type_name in _TIMELINE_TYPES:
        if wanted is not None and type_name not in wanted:
            continue

        query = select(
            literal(type_name).label("event_type"),
            model.id.label("event_id"),
            model.occurred_at.label("occurred_at"),
        ).where(
            model.owner_id == owner.id,
            model.id.not_in(
                select(model.supersedes_id).where(
                    model.owner_id == owner.id,
                    model.supersedes_id.is_not(None),
                )
            ),
        )
        if date_from:
            query = query.where(model.occurred_at >= date_from)
        if date_to:
            query = query.where(
                model.occurred_at < date_to
                if date_to_is_exclusive
                else model.occurred_at <= date_to
            )

        if model is DiaryEvent and not include_sensitive:
            query = query.where(DiaryEvent.is_sensitive.is_(False))
        if model is GarminMetricEvent:
            query = query.where(GarminMetricEvent.aggregation != "provider_sample")
        source_queries.append(query)

    if not source_queries:
        return TimelinePage(
            items=[],
            timezone=zone_name,
            page=page_metadata(0, pagination),
        )

    merged = (
        source_queries[0].subquery()
        if len(source_queries) == 1
        else union_all(*source_queries).subquery()
    )
    total_items = session.scalar(select(func.count()).select_from(merged)) or 0
    metadata = page_metadata(total_items, pagination)
    occurred_order = (
        merged.c.occurred_at.asc() if sort_order == "asc" else merged.c.occurred_at.desc()
    )
    keys = session.execute(
        select(merged.c.event_type, merged.c.event_id)
        .order_by(occurred_order, merged.c.event_type.asc(), merged.c.event_id.asc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    ).all()

    models_by_type: dict[str, type[EventMixin]] = {
        type_name: model for model, type_name in _TIMELINE_TYPES
    }
    selected: dict[tuple[str, uuid.UUID], EventMixin] = {}
    for type_name in {str(row.event_type) for row in keys}:
        model = models_by_type[type_name]
        ids = [row.event_id for row in keys if row.event_type == type_name]
        rows = session.scalars(select(model).where(model.owner_id == owner.id, model.id.in_(ids)))
        selected.update(((type_name, row.id), row) for row in rows)

    items = [
        _timeline_item(selected[(str(key.event_type), key.event_id)], str(key.event_type))
        for key in keys
    ]
    return TimelinePage(
        items=items,
        timezone=zone_name,
        page=metadata,
    )


def _timeline_item(row: EventMixin, type_name: str) -> TimelineItem:
    sensitive = bool(getattr(row, "is_sensitive", False))
    return TimelineItem(
        id=row.id,
        category="fact",
        event_type=type_name,
        summary=_summarize(row, type_name),
        time=time_out(row),
        provenance=provenance_out(row),
        is_sensitive=sensitive,
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
        case "meal":
            size = f" · size {row.size.value.upper()}" if row.size is not None else ""  # type: ignore[attr-defined]
            return f"Meal{size}"
        case "life_event":
            return f"{row.title} ({row.category})"  # type: ignore[attr-defined]
        case "emergency_injection":
            return f"Emergency injection {row.amount} {row.unit}"  # type: ignore[attr-defined]
        case "context":
            context = row  # type: ignore[assignment]
            if context.location_precision is LocationPrecision.COARSE:  # type: ignore[attr-defined]
                location = context.coarse_location_label  # type: ignore[attr-defined]
            elif context.location_precision is LocationPrecision.EXACT:  # type: ignore[attr-defined]
                location = "Exact location recorded (consent on file)"
            else:
                location = (
                    "Timezone context: "
                    f"{timezone_abbreviation(context.timezone, context.occurred_at)}"  # type: ignore[attr-defined]
                )
            conditions = context.conditions  # type: ignore[attr-defined]
            temperature = context.temperature  # type: ignore[attr-defined]
            apparent = context.apparent_temperature  # type: ignore[attr-defined]
            weather = []
            if conditions:
                weather.append(conditions)
            if temperature is not None:
                weather.append(f"{temperature} °C")
            if apparent is not None:
                weather.append(f"feels like {apparent} °C")
            return f"{location} · {'; '.join(weather)}" if weather else location
        case "blood_pressure":
            pulse = f"; pulse {row.pulse_bpm} bpm" if row.pulse_bpm is not None else ""  # type: ignore[attr-defined]
            reading = f"Blood pressure {row.systolic_mmhg}/{row.diastolic_mmhg} mmHg"  # type: ignore[attr-defined]
            setting = row.measurement_setting.value  # type: ignore[attr-defined]
            return f"{reading}{pulse} · {setting}"
        case "weight":
            pounds = vitals.display_weight_lb(row.value, row.unit)  # type: ignore[attr-defined]
            entered = (
                f" (entered {row.value} {row.unit})"  # type: ignore[attr-defined]
                if row.unit is not WeightUnit.LB  # type: ignore[attr-defined]
                else ""
            )
            return f"Weight {pounds} lb{entered} · {row.measurement_setting.value}"  # type: ignore[attr-defined]
        case "temperature":
            fahrenheit = vitals.display_temperature_f(row.value, row.unit)  # type: ignore[attr-defined]
            celsius = vitals.display_temperature_c(row.value, row.unit)  # type: ignore[attr-defined]
            return f"Temperature {fahrenheit} °F ({celsius} °C)"
        case "garmin_daily":
            return measurement_summary(  # type: ignore[arg-type]
                row.metric_type,  # type: ignore[attr-defined]
                row.garmin_field_name,  # type: ignore[attr-defined]
                row.value,  # type: ignore[attr-defined]
                row.unit,  # type: ignore[attr-defined]
            )
        case "garmin_sleep":
            duration = row.duration_seconds  # type: ignore[attr-defined]
            duration_text = (
                "duration unavailable"
                if duration is None
                else f"{duration // 3600}h {(duration % 3600) // 60}m"
            )
            score = row.overall_sleep_score  # type: ignore[attr-defined]
            score_text = "score unavailable" if score is None else f"score {score}"
            return f"Sleep: {duration_text}; {score_text}"
        case "garmin_activity":
            sport = row.sport.replace("_", " ").title()  # type: ignore[attr-defined]
            elapsed = row.elapsed_seconds  # type: ignore[attr-defined]
            duration_seconds = (
                int(elapsed)
                if elapsed is not None
                else max(0, int((row.ended_at - row.occurred_at).total_seconds()))  # type: ignore[attr-defined]
            )
            distance = row.distance_miles  # type: ignore[attr-defined]
            distance_text = "distance unavailable" if distance is None else f"{distance} mi"
            location = row.location_name  # type: ignore[attr-defined]
            location_text = "" if location is None else f"; {location}"
            return (
                f"Activity: {sport}; {_duration_text(duration_seconds)}; "
                f"{distance_text}{location_text}"
            )
        case _:  # pragma: no cover
            return type_name


def _duration_text(total_seconds: int) -> str:
    hours, remainder = divmod(max(0, total_seconds), 3600)
    minutes, seconds = divmod(remainder, 60)
    parts: list[str] = []
    if hours:
        parts.append(f"{hours}h")
    if minutes:
        parts.append(f"{minutes}m")
    if seconds or not parts:
        parts.append(f"{seconds}s")
    return " ".join(parts)


def _owned_symptom(session: DbSession, owner_id: uuid.UUID, event_id: uuid.UUID) -> SymptomEvent:
    row = session.get(SymptomEvent, event_id)
    if row is None or row.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return row


def _owned_meal(session: DbSession, owner_id: uuid.UUID, event_id: uuid.UUID) -> MealEvent:
    row = session.get(MealEvent, event_id)
    if row is None or row.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="record not found")
    return row
