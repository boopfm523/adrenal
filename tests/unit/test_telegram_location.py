"""Privacy and correlation tests for one-time Telegram phone location."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.context.models import ContextEvent, SavedCoarseLocation
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import handlers, location
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.dispatch import UpdateOutcome, process_update
from healthcurve.integrations.telegram.models import (
    LocationRequestState,
    TelegramLocationRequest,
)

OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000101")
DRAFT_ID = uuid.UUID("00000000-0000-4000-8000-000000000102")
NOW = datetime(2026, 8, 10, 15, 0, tzinfo=UTC)


def _owner() -> Owner:
    return Owner(
        id=OWNER_ID,
        email="location-owner@example.test",
        password_hash="synthetic-not-a-real-hash",
        default_timezone="America/New_York",
    )


def _draft(*, state: DraftState = DraftState.PENDING) -> ExtractionDraft:
    return ExtractionDraft(
        id=DRAFT_ID,
        owner_id=OWNER_ID,
        source="telegram",
        candidates=[],
        state=state,
        prompt_version="synthetic-v1",
        schema_version="synthetic-v1",
        created_at=NOW,
    )


def _request(
    *,
    state: LocationRequestState = LocationRequestState.PENDING,
    expires_at: datetime | None = None,
) -> TelegramLocationRequest:
    return TelegramLocationRequest(
        owner_id=OWNER_ID,
        chat_id=4242,
        draft_id=DRAFT_ID,
        state=state,
        requested_at=NOW,
        expires_at=expires_at or NOW + timedelta(minutes=10),
    )


def _session() -> tuple[Session, MagicMock]:
    mocked = MagicMock(spec=Session)
    return cast(Session, mocked), mocked


def test_phone_coordinates_are_rounded_before_persistence() -> None:
    assert location.round_phone_coordinates(40.71281, -74.00601) == (
        Decimal("40.7"),
        Decimal("-74.0"),
    )
    assert location.round_phone_coordinates(True, -74) is None
    assert location.round_phone_coordinates(91, 0) is None
    assert location.round_phone_coordinates("NaN", 0) is None


def test_location_button_requires_a_deliberate_telegram_permission_action() -> None:
    session, mocked = _session()
    mocked.get.return_value = _draft()
    mocked.scalar.return_value = None

    reply = handlers.start_location_request(session, _owner(), DRAFT_ID, chat_id=4242)

    assert reply.reply_markup is not None
    button = reply.reply_markup["keyboard"][0][0]
    assert button == {"text": "Share current location", "request_location": True}
    assert reply.reply_markup["one_time_keyboard"] is True


def test_exact_values_never_enter_the_request_object() -> None:
    session, mocked = _session()
    request = _request()
    mocked.scalar.return_value = request
    mocked.get.return_value = _draft()

    result = location.attach_phone_location(
        session,
        _owner(),
        chat_id=4242,
        latitude=40.71281,
        longitude=-74.00601,
        now=NOW,
    )

    assert result is location.LocationResult.ATTACHED
    assert request.rounded_latitude == Decimal("40.7")
    assert request.rounded_longitude == Decimal("-74.0")
    assert not hasattr(request, "exact_latitude")
    assert not hasattr(request, "exact_longitude")


def test_expired_or_unrelated_draft_cannot_receive_location() -> None:
    session, mocked = _session()
    expired = _request(expires_at=NOW - timedelta(seconds=1))
    mocked.scalar.return_value = expired

    result = location.attach_phone_location(
        session, _owner(), chat_id=4242, latitude=1.234, longitude=2.345, now=NOW
    )

    assert result is location.LocationResult.NO_PENDING_REQUEST
    assert expired.state is LocationRequestState.EXPIRED
    assert expired.rounded_latitude is None

    cancelled_draft_request = _request()
    mocked.scalar.return_value = cancelled_draft_request
    mocked.get.return_value = _draft(state=DraftState.CANCELLED)
    result = location.attach_phone_location(
        session, _owner(), chat_id=4242, latitude=1.234, longitude=2.345, now=NOW
    )
    assert result is location.LocationResult.NO_PENDING_REQUEST
    assert cancelled_draft_request.state is LocationRequestState.CANCELLED


def test_saved_home_is_rounded_and_explicitly_reused() -> None:
    session, mocked = _session()
    request = _request()
    home = SavedCoarseLocation(
        owner_id=OWNER_ID,
        name="home",
        label="Home area",
        latitude=Decimal("40.7"),
        longitude=Decimal("-74.0"),
        timezone="America/New_York",
    )
    mocked.scalar.side_effect = [request, home]
    mocked.get.return_value = _draft()

    result = location.attach_saved_home(session, _owner(), chat_id=4242, now=NOW)

    assert result is location.LocationResult.ATTACHED
    assert request.location_label == "Home area"
    assert request.rounded_latitude == Decimal("40.7")


def test_confirm_creates_only_coarse_context_then_purges_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, mocked = _session()
    request = _request(state=LocationRequestState.ATTACHED)
    request.rounded_latitude = Decimal("40.7")
    request.rounded_longitude = Decimal("-74.0")
    request.location_label = "Approximate phone location"
    mocked.scalar.return_value = request
    context = MagicMock(spec=ContextEvent)
    context.id = uuid.UUID("00000000-0000-4000-8000-000000000103")
    create = MagicMock(return_value=context)
    monkeypatch.setattr(location.events, "create_event", create)
    enqueue = MagicMock()
    monkeypatch.setattr(location, "enqueue_weather_enrichment", enqueue)

    created = location.consume_for_confirm(
        session, _owner(), draft_id=DRAFT_ID, now=NOW + timedelta(minutes=1)
    )

    assert created is context
    kwargs = create.call_args.kwargs
    assert kwargs["location_precision"].value == "coarse"
    assert kwargs["latitude"] == Decimal("40.7")
    assert kwargs["longitude"] == Decimal("-74.0")
    assert kwargs["exact_location_consent"] is False
    assert request.state is LocationRequestState.USED
    assert request.rounded_latitude is None
    assert request.rounded_longitude is None
    enqueue.assert_called_once_with(session, context)


def test_decline_and_expiry_clear_even_rounded_location() -> None:
    session, mocked = _session()
    request = _request(state=LocationRequestState.ATTACHED)
    request.rounded_latitude = Decimal("40.7")
    request.rounded_longitude = Decimal("-74.0")
    mocked.scalar.return_value = request
    mocked.get.return_value = _draft()

    assert (
        location.cancel_request(session, _owner(), chat_id=4242, now=NOW)
        is location.LocationResult.CANCELLED
    )
    assert request.rounded_latitude is None
    assert request.rounded_longitude is None


def test_dispatch_accepts_location_only_from_the_allowed_private_chat() -> None:
    session, mocked = _session()
    # Dispatch intentionally uses the real current clock. Keep this request active
    # regardless of when the deterministic 2026 fixture suite is executed.
    request = _request(expires_at=datetime.max.replace(tzinfo=UTC))
    draft = _draft()
    mocked.scalar.side_effect = [None, _owner(), request, draft]
    mocked.get.return_value = draft
    client = MagicMock(spec=TelegramClient)
    update = {
        "update_id": 77,
        "message": {
            "message_id": 88,
            "chat": {"id": 4242, "type": "private"},
            "location": {"latitude": 40.71281, "longitude": -74.00601},
        },
    }

    outcome = process_update(
        session, update, allowed_chat_id=4242, client=cast(TelegramClient, client)
    )

    assert outcome is UpdateOutcome.PROCESSED
    assert request.rounded_latitude == Decimal("40.7")
    sent_text = client.send_message.call_args.args[1]
    assert "Exact coordinates were not stored" in sent_text
    assert "40.71281" not in sent_text


def test_group_location_is_denied_without_touching_request_state() -> None:
    session, mocked = _session()
    mocked.scalar.side_effect = [None, _owner()]
    client = MagicMock(spec=TelegramClient)
    update = {
        "update_id": 78,
        "message": {
            "message_id": 89,
            "chat": {"id": 4242, "type": "group"},
            "location": {"latitude": 40.71281, "longitude": -74.00601},
        },
    }

    outcome = process_update(
        session, update, allowed_chat_id=4242, client=cast(TelegramClient, client)
    )

    assert outcome is UpdateOutcome.IGNORED
    assert "private chat" in client.send_message.call_args.args[1]
    mocked.get.assert_not_called()
