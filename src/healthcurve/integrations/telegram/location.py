"""Privacy boundary for one-time Telegram phone locations.

Exact coordinates exist only as function arguments. They are rounded before an ORM
object is constructed, so database logs and backups cannot contain raw phone GPS.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from enum import StrEnum
from typing import Final

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.ai.models import ExtractionDraft
from healthcurve.context.models import ContextEvent, LocationPrecision, SavedCoarseLocation
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.timekeeping import from_instant
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram.models import LocationRequestState, TelegramLocationRequest
from healthcurve.integrations.weather.jobs import enqueue_weather_enrichment

LOCATION_REQUEST_TTL: Final = timedelta(minutes=10)
ROUNDING_QUANTUM: Final = Decimal("0.1")
HOME_NAME: Final = "home"


class LocationResult(StrEnum):
    ATTACHED = "attached"
    NO_PENDING_REQUEST = "no_pending_request"
    INVALID = "invalid"
    NO_HOME = "no_home"
    CANCELLED = "cancelled"


def round_phone_coordinates(latitude: object, longitude: object) -> tuple[Decimal, Decimal] | None:
    """Return one-decimal coordinates without retaining the raw values."""
    if isinstance(latitude, bool) or isinstance(longitude, bool):
        return None
    try:
        lat, lon = Decimal(str(latitude)), Decimal(str(longitude))
    except (InvalidOperation, ValueError):
        return None
    if not lat.is_finite() or not lon.is_finite():
        return None
    if not (Decimal("-90") <= lat <= Decimal("90")):
        return None
    if not (Decimal("-180") <= lon <= Decimal("180")):
        return None
    return (
        lat.quantize(ROUNDING_QUANTUM, rounding=ROUND_HALF_UP),
        lon.quantize(ROUNDING_QUANTUM, rounding=ROUND_HALF_UP),
    )


def begin_request(
    session: Session,
    owner: Owner,
    *,
    chat_id: int,
    draft_id: uuid.UUID,
    now: datetime | None = None,
) -> TelegramLocationRequest | None:
    now = now or datetime.now(UTC)
    draft = session.get(ExtractionDraft, draft_id)
    if draft is None or draft.owner_id != owner.id or not draft.is_pending:
        return None
    _cancel_active(session, owner.id, now=now)
    request = TelegramLocationRequest(
        owner_id=owner.id,
        chat_id=chat_id,
        draft_id=draft.id,
        state=LocationRequestState.PENDING,
        requested_at=now,
        expires_at=now + LOCATION_REQUEST_TTL,
    )
    session.add(request)
    session.flush()
    return request


def attach_phone_location(
    session: Session,
    owner: Owner,
    *,
    chat_id: int,
    latitude: object,
    longitude: object,
    now: datetime | None = None,
) -> LocationResult:
    request = _active(session, owner.id, chat_id, now=now or datetime.now(UTC))
    if request is None:
        return LocationResult.NO_PENDING_REQUEST
    rounded = round_phone_coordinates(latitude, longitude)
    if rounded is None:
        return LocationResult.INVALID
    request.rounded_latitude, request.rounded_longitude = rounded
    request.location_label = "Approximate phone location"
    request.state = LocationRequestState.ATTACHED
    return LocationResult.ATTACHED


def attach_saved_home(
    session: Session,
    owner: Owner,
    *,
    chat_id: int,
    now: datetime | None = None,
) -> LocationResult:
    request = _active(session, owner.id, chat_id, now=now or datetime.now(UTC))
    if request is None:
        return LocationResult.NO_PENDING_REQUEST
    home = session.scalar(
        select(SavedCoarseLocation).where(
            SavedCoarseLocation.owner_id == owner.id,
            SavedCoarseLocation.name == HOME_NAME,
        )
    )
    if home is None:
        return LocationResult.NO_HOME
    request.rounded_latitude = home.latitude
    request.rounded_longitude = home.longitude
    request.location_label = home.label
    request.state = LocationRequestState.ATTACHED
    return LocationResult.ATTACHED


def cancel_request(
    session: Session,
    owner: Owner,
    *,
    chat_id: int,
    now: datetime | None = None,
) -> LocationResult:
    now = now or datetime.now(UTC)
    request = _active(session, owner.id, chat_id, now=now)
    if request is None:
        return LocationResult.NO_PENDING_REQUEST
    _resolve(request, LocationRequestState.CANCELLED, now=now)
    return LocationResult.CANCELLED


def save_attached_as_home(session: Session, owner: Owner, *, draft_id: uuid.UUID) -> bool:
    request = _for_draft(session, owner.id, draft_id, LocationRequestState.ATTACHED)
    if request is None or request.rounded_latitude is None or request.rounded_longitude is None:
        return False
    home = session.scalar(
        select(SavedCoarseLocation).where(
            SavedCoarseLocation.owner_id == owner.id,
            SavedCoarseLocation.name == HOME_NAME,
        )
    )
    if home is None:
        home = SavedCoarseLocation(
            owner_id=owner.id,
            name=HOME_NAME,
            label="Home area",
            latitude=request.rounded_latitude,
            longitude=request.rounded_longitude,
            timezone=owner.default_timezone,
        )
        session.add(home)
    else:
        home.latitude = request.rounded_latitude
        home.longitude = request.rounded_longitude
        home.timezone = owner.default_timezone
    return True


def consume_for_confirm(
    session: Session,
    owner: Owner,
    *,
    draft_id: uuid.UUID,
    now: datetime | None = None,
) -> ContextEvent | None:
    now = now or datetime.now(UTC)
    request = _for_draft(session, owner.id, draft_id, LocationRequestState.ATTACHED)
    if request is None or request.expires_at <= now:
        if request is not None:
            _resolve(request, LocationRequestState.EXPIRED, now=now)
        return None
    if request.rounded_latitude is None or request.rounded_longitude is None:
        return None
    context = events.create_event(
        session,
        ContextEvent,
        owner_id=owner.id,
        event_time=from_instant(request.requested_at, owner.default_timezone),
        source_type=SourceType.TELEGRAM,
        confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
        location_precision=LocationPrecision.COARSE,
        coarse_location_label=request.location_label or "Approximate location",
        latitude=request.rounded_latitude,
        longitude=request.rounded_longitude,
        exact_location_consent=False,
        provider_id=f"telegram-location:{draft_id}",
        source_revision="rounded-0.1-v1",
    )
    enqueue_weather_enrichment(session, context)
    _resolve(request, LocationRequestState.USED, now=now)
    return context


def cancel_for_draft(
    session: Session,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    *,
    now: datetime | None = None,
) -> None:
    request = _for_draft(session, owner_id, draft_id)
    if request is not None:
        _resolve(request, LocationRequestState.CANCELLED, now=now or datetime.now(UTC))


def expire_requests(session: Session, *, now: datetime | None = None) -> int:
    now = now or datetime.now(UTC)
    requests = session.scalars(
        select(TelegramLocationRequest).where(
            TelegramLocationRequest.state.in_(
                (LocationRequestState.PENDING, LocationRequestState.ATTACHED)
            ),
            TelegramLocationRequest.expires_at <= now,
        )
    ).all()
    for request in requests:
        _resolve(request, LocationRequestState.EXPIRED, now=now)
    return len(requests)


def _active(
    session: Session, owner_id: uuid.UUID, chat_id: int, *, now: datetime
) -> TelegramLocationRequest | None:
    request = session.scalar(
        select(TelegramLocationRequest)
        .where(
            TelegramLocationRequest.owner_id == owner_id,
            TelegramLocationRequest.chat_id == chat_id,
            TelegramLocationRequest.state.in_(
                (LocationRequestState.PENDING, LocationRequestState.ATTACHED)
            ),
        )
        .order_by(TelegramLocationRequest.requested_at.desc())
        .limit(1)
    )
    if request is not None and request.expires_at <= now:
        _resolve(request, LocationRequestState.EXPIRED, now=now)
        return None
    if request is not None:
        draft = session.get(ExtractionDraft, request.draft_id)
        if draft is None or draft.owner_id != owner_id or not draft.is_pending:
            _resolve(request, LocationRequestState.CANCELLED, now=now)
            return None
    return request


def _for_draft(
    session: Session,
    owner_id: uuid.UUID,
    draft_id: uuid.UUID,
    state: LocationRequestState | None = None,
) -> TelegramLocationRequest | None:
    states = (
        (state,)
        if state is not None
        else (LocationRequestState.PENDING, LocationRequestState.ATTACHED)
    )
    return session.scalar(
        select(TelegramLocationRequest).where(
            TelegramLocationRequest.owner_id == owner_id,
            TelegramLocationRequest.draft_id == draft_id,
            TelegramLocationRequest.state.in_(states),
        )
    )


def _cancel_active(session: Session, owner_id: uuid.UUID, *, now: datetime) -> None:
    request = session.scalar(
        select(TelegramLocationRequest).where(
            TelegramLocationRequest.owner_id == owner_id,
            TelegramLocationRequest.state.in_(
                (LocationRequestState.PENDING, LocationRequestState.ATTACHED)
            ),
        )
    )
    if request is not None:
        _resolve(request, LocationRequestState.CANCELLED, now=now)


def _resolve(
    request: TelegramLocationRequest, state: LocationRequestState, *, now: datetime
) -> None:
    request.state = state
    request.resolved_at = now
    request.rounded_latitude = None
    request.rounded_longitude = None
    request.location_label = None
