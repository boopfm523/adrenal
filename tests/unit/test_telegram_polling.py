"""Long-polling transport (ADR-0008).

The interesting cases are the ones a personal machine actually hits: the network
dropping, Telegram returning nonsense, a poisoned update, and a redelivery after a
crash. None of them may stop the loop or lose the offset.
"""

from __future__ import annotations

import threading
from typing import Any

import httpx
import pytest

from healthcurve.config import Settings, TelegramMode
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.dispatch import chat_id_of
from healthcurve.integrations.telegram.polling import (
    ALLOWED_UPDATES,
    INITIAL_BACKOFF_SECONDS,
    MAX_BACKOFF_SECONDS,
    POLL_TIMEOUT_SECONDS,
    TelegramNotConfiguredError,
    TelegramPoller,
)

ALLOWED_CHAT = 4242


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,  # never read the developer's .env
        "ollama_base_url": "http://ollama:11434",
        "telegram_bot_token": "123:ABC",
        "telegram_webhook_secret": "s3cret",
        "telegram_allowed_chat_id": ALLOWED_CHAT,
    }
    base.update(overrides)
    return Settings(**base)


def _update(update_id: int, *, chat_id: int = ALLOWED_CHAT, text: str = "hello") -> dict[str, Any]:
    return {
        "update_id": update_id,
        "message": {"message_id": update_id, "chat": {"id": chat_id}, "text": text},
    }


class FakeClient(TelegramClient):
    """Records outbound calls instead of making them."""

    def __init__(self, settings: Settings, webhook_url: str | None = None) -> None:
        super().__init__(settings)
        self.sent: list[str] = []
        self.deleted_webhook_with: list[bool] = []
        self._webhook_url = webhook_url

    def send_message(self, chat_id: Any, text: str, **kwargs: Any) -> bool:
        self.sent.append(text)
        return True

    def get_webhook_info(self) -> dict[str, Any] | None:
        return {"ok": True, "result": {"url": self._webhook_url or ""}}

    def delete_webhook(self, *, drop_pending_updates: bool = True) -> dict[str, Any] | None:
        self.deleted_webhook_with.append(drop_pending_updates)
        self._webhook_url = None
        return {"ok": True}


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


def test_polling_is_the_default_mode() -> None:
    """ADR-0008: works with no public endpoint, so it is the safe default."""
    assert _settings().telegram_mode is TelegramMode.POLLING


def test_polling_refuses_to_start_without_an_allow_list() -> None:
    """An unrestricted bot would process anyone's messages."""
    poller = TelegramPoller(_settings(telegram_allowed_chat_id=None))
    with pytest.raises(TelegramNotConfiguredError, match="ALLOWED_CHAT_ID"):
        poller.run_forever()


def test_read_timeout_exceeds_the_long_poll_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    """Otherwise every quiet interval would look like a network failure."""
    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["timeout"] = request.extensions.get("timeout", {})
        return httpx.Response(200, json={"ok": True, "result": []})

    _patch_transport(monkeypatch, handler)
    TelegramPoller(_settings()).fetch_updates()
    assert seen["timeout"]["read"] > POLL_TIMEOUT_SECONDS


def test_poll_requests_only_the_update_types_we_handle(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json

    seen: dict[str, Any] = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen.update(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": []})

    _patch_transport(monkeypatch, handler)
    TelegramPoller(_settings()).fetch_updates()
    assert seen["allowed_updates"] == ALLOWED_UPDATES
    assert seen["timeout"] == POLL_TIMEOUT_SECONDS
    assert "offset" not in seen, "the first poll has no offset to send"


# ---------------------------------------------------------------------------
# Offset handling
# ---------------------------------------------------------------------------


def test_offset_advances_even_when_processing_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stuck poller is worse than one lost message the owner can resend.

    Without this, Telegram would redeliver the same poisoned update forever and no
    later message would ever be seen.
    """
    monkeypatch.setattr(
        "healthcurve.integrations.telegram.polling.get_session_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("database down")),
    )
    poller = TelegramPoller(_settings(), client=FakeClient(_settings()))

    poller.handle_one(_update(7))

    assert poller.offset == 8
    assert poller.stats.errors == 1


def test_sent_offset_is_the_next_expected_update(monkeypatch: pytest.MonkeyPatch) -> None:
    import json

    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json={"ok": True, "result": []})

    _patch_transport(monkeypatch, handler)
    monkeypatch.setattr(
        "healthcurve.integrations.telegram.polling.get_session_factory",
        lambda: (_ for _ in ()).throw(RuntimeError("no database in this test")),
    )
    poller = TelegramPoller(_settings(), client=FakeClient(_settings()))
    poller.handle_one(_update(55))
    poller.fetch_updates()

    assert seen[-1]["offset"] == 56


# ---------------------------------------------------------------------------
# Failure handling
# ---------------------------------------------------------------------------


def test_network_failure_does_not_raise_out_of_the_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("no route to host", request=request)

    _patch_transport(monkeypatch, handler)
    poller = TelegramPoller(_settings(), client=FakeClient(_settings()))

    with pytest.raises(httpx.ConnectError):
        poller.fetch_updates()  # fetch itself propagates...

    # ...but the loop absorbs it and backs off rather than dying.
    stop = threading.Event()
    poller = TelegramPoller(_settings(), client=FakeClient(_settings()), stop_event=stop)
    calls = {"n": 0}

    def failing_fetch() -> list[dict[str, Any]]:
        calls["n"] += 1
        if calls["n"] >= 3:
            stop.set()
        raise httpx.ConnectError("no route to host", request=httpx.Request("POST", "http://x"))

    monkeypatch.setattr(poller, "fetch_updates", failing_fetch)
    monkeypatch.setattr(poller, "prepare", lambda: None)

    def no_wait(_timeout: float | None = None) -> bool:
        return False

    monkeypatch.setattr(stop, "wait", no_wait)

    poller.run_forever()  # must return, not raise
    assert poller.stats.errors >= 3


def test_backoff_is_capped() -> None:
    """A long outage must not turn into a long silence once the network returns."""
    assert INITIAL_BACKOFF_SECONDS < MAX_BACKOFF_SECONDS
    assert MAX_BACKOFF_SECONDS <= 60


def test_non_ok_response_yields_no_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"ok": False, "description": "flood wait"})

    _patch_transport(monkeypatch, handler)
    assert TelegramPoller(_settings()).fetch_updates() == []


# ---------------------------------------------------------------------------
# Webhook interaction
# ---------------------------------------------------------------------------


def test_prepare_removes_an_existing_webhook_without_dropping_messages() -> None:
    """Telegram refuses getUpdates while a webhook is set, but queued updates are
    real messages the owner sent."""
    settings = _settings()
    client = FakeClient(settings, webhook_url="https://old.example.com/hook")
    TelegramPoller(settings, client=client).prepare()

    assert client.deleted_webhook_with == [False], "pending updates must be kept"


def test_prepare_does_nothing_when_no_webhook_is_set() -> None:
    settings = _settings()
    client = FakeClient(settings, webhook_url=None)
    TelegramPoller(settings, client=client).prepare()
    assert client.deleted_webhook_with == []


# ---------------------------------------------------------------------------
# Shared dispatch
# ---------------------------------------------------------------------------


def test_chat_id_is_read_from_both_message_and_callback_shapes() -> None:
    assert chat_id_of(_update(1)) == ALLOWED_CHAT
    assert (
        chat_id_of({"update_id": 2, "callback_query": {"message": {"chat": {"id": ALLOWED_CHAT}}}})
        == ALLOWED_CHAT
    )


def test_chat_id_of_untrusted_shapes_is_none() -> None:
    """Untrusted JSON must narrow to None rather than something truthy."""
    for update in (
        {"update_id": 1},
        {"update_id": 2, "message": {}},
        {"update_id": 3, "message": {"chat": {"id": "not-an-int"}}},
    ):
        assert chat_id_of(update) is None


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: Any) -> None:
    original = httpx.Client.__init__

    def fake_init(self: httpx.Client, **kwargs: Any) -> None:
        original(self, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", fake_init)
