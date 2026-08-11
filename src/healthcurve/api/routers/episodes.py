"""Stress/up-dose episodes and emergency injections."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy import func, select

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.pagination import Pagination, page_metadata, paginate_current_facts
from healthcurve.api.routers.doses import resolve_time
from healthcurve.api.routers.events import provenance_out, time_out
from healthcurve.api.schemas import (
    EpisodeIn,
    EpisodeOut,
    EpisodePage,
    EpisodeUpdate,
    InjectionIn,
    InjectionOut,
    InjectionPage,
)
from healthcurve.episodes.models import (
    EmergencyInjectionEvent,
    EpisodeStatus,
    StressEpisode,
)
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import SymptomEvent
from healthcurve.medications.models import DoseEvent, Medication

router = APIRouter(tags=["episodes"])


@router.post(
    "/stress-episodes",
    response_model=EpisodeOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_episode(payload: EpisodeIn, session: DbSession, owner: CurrentOwner):
    started = resolve_time(payload.time)
    episode = StressEpisode(
        owner_id=owner.id,
        trigger=payload.trigger,
        severity=payload.severity,
        status=EpisodeStatus.OPEN,
        started_at=started.occurred_at,
        ended_at=None,
        timezone=started.timezone,
        highest_temperature_c=payload.highest_temperature_c,
        illness_description=payload.illness_description,
        notes=payload.notes,
        recorded_at=datetime.now(UTC),
    )
    session.add(episode)
    session.flush()
    return _episode_out(session, episode)


@router.get("/stress-episodes", response_model=EpisodePage)
def list_episodes(
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
    status_filter: EpisodeStatus | None = None,
    open_only: bool = False,
) -> EpisodePage:
    query = select(StressEpisode).where(StressEpisode.owner_id == owner.id)
    if status_filter is not None:
        query = query.where(StressEpisode.status == status_filter)
    elif open_only:
        query = query.where(StressEpisode.status == EpisodeStatus.OPEN)
    total_items = session.scalar(select(func.count()).select_from(query.subquery())) or 0
    metadata = page_metadata(total_items, pagination)
    rows = session.scalars(
        query.order_by(StressEpisode.started_at.desc(), StressEpisode.id.asc())
        .offset(pagination.offset)
        .limit(pagination.page_size)
    )
    return EpisodePage(items=[_episode_out(session, row) for row in rows], page=metadata)


@router.patch(
    "/stress-episodes/{episode_id}",
    response_model=EpisodeOut,
    dependencies=[Depends(require_csrf)],
)
def update_episode(
    episode_id: uuid.UUID, payload: EpisodeUpdate, session: DbSession, owner: CurrentOwner
):
    """Update an open episode.

    An episode is a container the owner curates while it is happening, so it is
    genuinely mutable -- unlike the recorded facts inside it, which are corrected by
    supersession.
    """
    episode = _owned_episode(session, owner.id, episode_id)
    changes = payload.model_dump(exclude_unset=True)
    ended_at = changes.pop("ended_at", None)
    for field, value in changes.items():
        setattr(episode, field, value)

    if "ended_at" in payload.model_fields_set:
        episode.ended_at = None if ended_at is None else resolve_time(payload.ended_at).occurred_at

    if episode.status is EpisodeStatus.RESOLVED and episode.ended_at is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail="a resolved episode needs an end time",
        )
    session.flush()
    return _episode_out(session, episode)


@router.post(
    "/emergency-injections",
    response_model=InjectionOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def log_injection(payload: InjectionIn, session: DbSession, owner: CurrentOwner):
    """Log an emergency injection.

    Deliberately depends on nothing but the database: no AI call, no integration, no
    background job (SAFE-23). Everything beyond medication, amount, unit, and time is
    optional, because a partial record now beats a complete record later.
    """
    medication = session.get(Medication, payload.medication_id)
    if medication is None or medication.owner_id != owner.id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="medication not found")

    injection = events.create_event(
        session,
        EmergencyInjectionEvent,
        owner_id=owner.id,
        event_time=resolve_time(payload.time),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        medication_id=payload.medication_id,
        amount=payload.amount,
        unit=payload.unit,
        route=payload.route,
        injection_site=payload.injection_site,
        reason=payload.reason,
        injected_by=payload.injected_by,
        response=payload.response,
        emergency_services_called=payload.emergency_services_called,
        transported_to_hospital=payload.transported_to_hospital,
        contact_notified=payload.contact_notified,
        episode_id=payload.episode_id,
    )
    return _injection_out(injection)


@router.get("/emergency-injections", response_model=InjectionPage)
def list_injections(
    session: DbSession, owner: CurrentOwner, pagination: Pagination
) -> InjectionPage:
    page = paginate_current_facts(
        session,
        EmergencyInjectionEvent,
        owner_id=owner.id,
        request=pagination,
    )
    return InjectionPage(
        items=[_injection_out(row) for row in page.items],
        revisions=[_injection_out(row) for row in page.revisions],
        page=page.metadata,
    )


def _owned_episode(session: DbSession, owner_id: uuid.UUID, episode_id: uuid.UUID) -> StressEpisode:
    episode = session.get(StressEpisode, episode_id)
    if episode is None or episode.owner_id != owner_id:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="episode not found")
    return episode


def _episode_out(session: DbSession, e: StressEpisode) -> EpisodeOut:
    dose_count = session.scalar(
        select(func.count()).select_from(DoseEvent).where(DoseEvent.episode_id == e.id)
    )
    symptom_count = session.scalar(
        select(func.count()).select_from(SymptomEvent).where(SymptomEvent.episode_id == e.id)
    )
    return EpisodeOut(
        id=e.id,
        trigger=e.trigger,
        status=e.status,
        severity=e.severity,
        started_at=e.started_at,
        ended_at=e.ended_at,
        timezone=e.timezone,
        highest_temperature_c=e.highest_temperature_c,
        illness_description=e.illness_description,
        recovery_notes=e.recovery_notes,
        outcome=e.outcome,
        notes=e.notes,
        dose_count=dose_count or 0,
        symptom_count=symptom_count or 0,
    )


def _injection_out(e: EmergencyInjectionEvent) -> InjectionOut:
    return InjectionOut(
        id=e.id,
        medication_id=e.medication_id,
        amount=e.amount,
        unit=e.unit,
        route=e.route,
        time=time_out(e),
        provenance=provenance_out(e),
        injection_site=e.injection_site,
        reason=e.reason,
        injected_by=e.injected_by,
        response=e.response,
        emergency_services_called=e.emergency_services_called,
        transported_to_hospital=e.transported_to_hospital,
        episode_id=e.episode_id,
    )
