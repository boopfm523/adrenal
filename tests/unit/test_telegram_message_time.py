from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from healthcurve.ai.models import ExtractionDraft
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import handlers
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.dispatch import (
    UpdateOutcome,
    message_sent_at_of,
    process_update,
)
from healthcurve.integrations.telegram.models import TelegramUpdate

PROCESSING_TIME = datetime(2026, 8, 13, 18, 0, tzinfo=UTC)
SENT_TIME = datetime(2026, 8, 13, 12, 30, tzinfo=UTC)


class FakeTelegramClient:
    def send_message(
        self, chat_id: int, text: str, *, reply_markup: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        del chat_id, text, reply_markup
        return {"ok": True}


def _owner() -> Owner:
    return Owner(
        id=uuid.uuid4(),
        email="telegram-time-owner@example.test",
        password_hash="synthetic-not-a-real-hash",
        default_timezone="America/New_York",
    )


@pytest.mark.parametrize(
    "text",
    ["/weight 180 lb", "Took 10 mg hydrocortisone"],
    ids=["command", "free-text"],
)
def test_dispatch_uses_delayed_message_send_time_for_all_text_paths(
    monkeypatch: pytest.MonkeyPatch, text: str
) -> None:
    owner = _owner()
    mocked = MagicMock(spec=Session)
    mocked.scalar.side_effect = [None, owner]
    captured: dict[str, Any] = {}

    def fake_handle_message(
        session: Session,
        received_owner: Owner,
        **kwargs: Any,
    ) -> handlers.Reply:
        del session, received_owner
        captured.update(kwargs)
        return handlers.Reply("synthetic reply")

    monkeypatch.setattr(handlers, "handle_message", fake_handle_message)
    outcome = process_update(
        cast(Session, mocked),
        {
            "update_id": 7001,
            "message": {
                "message_id": 9001,
                "date": int(SENT_TIME.timestamp()),
                "chat": {"id": 4242, "type": "private"},
                "text": text,
            },
        },
        allowed_chat_id=4242,
        client=cast(TelegramClient, FakeTelegramClient()),
        now=PROCESSING_TIME,
    )

    assert outcome is UpdateOutcome.PROCESSED
    assert captured["now"] == SENT_TIME
    claim = mocked.add.call_args_list[0].args[0]
    assert isinstance(claim, TelegramUpdate)
    assert claim.provider_message_id == 9001
    assert claim.provider_sent_at == SENT_TIME
    assert claim.received_at == PROCESSING_TIME
    assert claim.reference_time_source == "telegram_message_date"


@pytest.mark.parametrize("date_value", [None, "not-a-timestamp", True, 10**30])
def test_invalid_or_missing_message_date_uses_processing_time_fallback(
    monkeypatch: pytest.MonkeyPatch, date_value: object
) -> None:
    owner = _owner()
    mocked = MagicMock(spec=Session)
    mocked.scalar.side_effect = [None, owner]
    captured: dict[str, Any] = {}

    def fake_handle_message(
        session: Session,
        received_owner: Owner,
        **kwargs: Any,
    ) -> handlers.Reply:
        del session, received_owner
        captured.update(kwargs)
        return handlers.Reply("ok")

    monkeypatch.setattr(
        handlers,
        "handle_message",
        fake_handle_message,
    )
    message: dict[str, Any] = {
        "message_id": 9002,
        "chat": {"id": 4242, "type": "private"},
        "text": "/weight 180 lb",
    }
    if date_value is not None:
        message["date"] = date_value

    process_update(
        cast(Session, mocked),
        {"update_id": 7002, "message": message},
        allowed_chat_id=4242,
        client=cast(TelegramClient, FakeTelegramClient()),
        now=PROCESSING_TIME,
    )

    assert captured["now"] == PROCESSING_TIME
    claim = mocked.add.call_args_list[0].args[0]
    assert claim.provider_sent_at is None
    assert claim.reference_time_source == "processing_time_fallback"


def test_future_message_date_is_not_used_as_health_event_time() -> None:
    message = {"date": int((PROCESSING_TIME + timedelta(minutes=6)).timestamp())}
    assert message_sent_at_of(message, processing_time=PROCESSING_TIME) is None


def test_explicit_command_time_remains_authoritative_over_send_time() -> None:
    owner = _owner()
    mocked = MagicMock(spec=Session)
    mocked.scalar.return_value = None

    handlers.handle_message(
        cast(Session, mocked),
        owner,
        text="/weight 180 lb 07:15",
        now=SENT_TIME,
    )

    draft = mocked.add.call_args.args[0]
    assert isinstance(draft, ExtractionDraft)
    assert draft.candidates[0]["local_time"] == "2026-08-13T07:15:00"


def test_no_time_command_uses_send_time_in_owner_timezone() -> None:
    owner = _owner()
    mocked = MagicMock(spec=Session)
    mocked.scalar.return_value = None

    handlers.handle_message(
        cast(Session, mocked),
        owner,
        text="/weight 180 lb",
        now=SENT_TIME,
    )

    draft = mocked.add.call_args.args[0]
    assert isinstance(draft, ExtractionDraft)
    assert draft.candidates[0]["local_time"] == "2026-08-13T08:30:00"
