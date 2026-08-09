"""Long-polling loop for Telegram (ADR-0008).

The default transport. The process calls ``getUpdates`` outbound and holds the
connection open, so HealthCurve needs no public endpoint, no certificate, no DNS entry
and no inbound firewall rule. This is what lets the bot run on a laptop behind NAT or
on a machine reachable only over a private tailnet.

Designed for a personal machine, which means assuming the network will misbehave: the
laptop sleeps, the connection changes, Telegram is briefly unreachable. None of that
may lose the offset or stop the loop.
"""

from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field
from typing import Any, Final

import httpx
from pydantic import SecretStr

from healthcurve.config import Settings, get_settings
from healthcurve.db import get_session_factory
from healthcurve.integrations.telegram.client import API_BASE, TelegramClient
from healthcurve.integrations.telegram.dispatch import UpdateOutcome, process_update
from healthcurve.integrations.telegram.secrets import TelegramSecrets
from healthcurve.logging import get_logger
from healthcurve.operations.rate_limit import RateLimiter, RateLimitPolicy

log = get_logger(__name__)

#: How long Telegram holds the request open with no updates. Long enough that idle
#: polling is nearly free, short enough that a stop signal is acted on promptly.
POLL_TIMEOUT_SECONDS: Final = 25

#: Backoff between failed polls. Capped so a long outage does not become a long silence
#: once the network returns.
INITIAL_BACKOFF_SECONDS: Final = 1.0
MAX_BACKOFF_SECONDS: Final = 60.0

#: Only these are requested. Telegram does not deliver update types we do not ask for.
ALLOWED_UPDATES: Final = ["message", "callback_query"]


class TelegramNotConfiguredError(RuntimeError):
    """Polling was started without a token, a chat allow-list, or both."""


@dataclass
class PollerStats:
    """Counters for the operations view. Deliberately holds no message content."""

    polls: int = 0
    updates_received: int = 0
    processed: int = 0
    duplicates: int = 0
    rejected: int = 0
    errors: int = 0
    outcomes: dict[str, int] = field(default_factory=dict)


class TelegramPoller:
    """Fetches updates and hands each to the shared dispatcher."""

    def __init__(
        self,
        settings: Settings | None = None,
        *,
        token: SecretStr | None = None,
        client: TelegramClient | None = None,
        stop_event: threading.Event | None = None,
    ) -> None:
        self._settings = settings or get_settings()
        self._token_value = token or self._settings.telegram_bot_token
        self._client = client or TelegramClient(self._settings, token=self._token_value)
        self._stop = stop_event or threading.Event()
        self._limiter = RateLimiter(self._settings.redis_url)
        self._offset: int | None = None
        self.stats = PollerStats()

    # -- lifecycle ---------------------------------------------------------

    def stop(self) -> None:
        self._stop.set()

    @property
    def offset(self) -> int | None:
        return self._offset

    def _token(self) -> str:
        token = self._token_value
        if token is None:
            raise TelegramNotConfiguredError("HC_TELEGRAM_BOT_TOKEN is not set")
        return token.get_secret_value()

    def _allowed_chat_id(self) -> int:
        chat_id = self._settings.telegram_allowed_chat_id
        if chat_id is None:
            raise TelegramNotConfiguredError(
                "HC_TELEGRAM_ALLOWED_CHAT_ID is not set; the bot must never process "
                "messages from an unrestricted set of chats"
            )
        return chat_id

    def prepare(self) -> None:
        """Clear any registered webhook.

        Telegram refuses getUpdates with 409 while a webhook is set, and running both
        would double-deliver every message. Pending updates are kept: they are real
        messages the owner sent, and the offset plus the deduplication table make
        replay safe.
        """
        info = self._client.get_webhook_info()
        if info and info.get("ok") and (info["result"] or {}).get("url"):
            log.info(
                "removing webhook before polling",
                integration="telegram",
                reason_code="polling_mode",
            )
            self._client.delete_webhook(drop_pending_updates=False)

    # -- the loop ----------------------------------------------------------

    def run_forever(self) -> None:
        """Poll until stopped. Never raises for an expected failure."""
        self._allowed_chat_id()  # fail fast rather than polling into a misconfiguration
        self.prepare()

        log.info(
            "telegram poller started",
            integration="telegram",
            outcome="polling",
        )
        backoff = INITIAL_BACKOFF_SECONDS

        while not self._stop.is_set():
            try:
                updates = self.fetch_updates()
            except TelegramNotConfiguredError:
                raise
            except Exception as exc:
                self.stats.errors += 1
                log.warning(
                    "telegram poll failed",
                    integration="telegram",
                    outcome="failed",
                    reason_code=type(exc).__name__,
                )
                # Wait on the stop event rather than sleeping, so shutdown stays prompt.
                self._stop.wait(backoff)
                backoff = min(backoff * 2, MAX_BACKOFF_SECONDS)
                continue

            backoff = INITIAL_BACKOFF_SECONDS
            for update in updates:
                self.handle_one(update)

        log.info("telegram poller stopped", integration="telegram", outcome="clean")

    def fetch_updates(self) -> list[dict[str, Any]]:
        """One long poll. Returns the updates, or an empty list on a quiet interval."""
        params: dict[str, Any] = {
            "timeout": POLL_TIMEOUT_SECONDS,
            "allowed_updates": ALLOWED_UPDATES,
        }
        if self._offset is not None:
            params["offset"] = self._offset

        # Read timeout must exceed the long-poll timeout, or every quiet interval
        # would look like a network failure.
        timeout = httpx.Timeout(
            connect=10.0,
            read=POLL_TIMEOUT_SECONDS + 15,
            write=10.0,
            pool=10.0,
        )
        with httpx.Client(timeout=timeout) as client:
            response = client.post(f"{API_BASE}/bot{self._token()}/getUpdates", json=params)
            response.raise_for_status()
            body = response.json()

        self.stats.polls += 1
        if not body.get("ok"):
            return []

        updates: list[dict[str, Any]] = body.get("result") or []
        self.stats.updates_received += len(updates)
        return updates

    def handle_one(self, update: dict[str, Any]) -> UpdateOutcome:
        """Process one update in its own transaction, then advance the offset.

        Each update gets its own transaction so one bad message cannot roll back the
        rest of a batch. The offset advances even when processing failed -- Telegram
        would otherwise redeliver the same broken update forever, and a stuck poller is
        worse than a lost message the owner can resend.
        """
        update_id = update.get("update_id")
        outcome = UpdateOutcome.IGNORED

        try:
            # Inside the try: acquiring the factory can fail too (bad URL, pool
            # exhausted), and that must back off like any other failure rather than
            # killing the loop.
            factory = get_session_factory()
            with factory() as session, session.begin():
                outcome = process_update(
                    session,
                    update,
                    allowed_chat_id=self._allowed_chat_id(),
                    client=self._client,
                    limiter=self._limiter,
                    model_policy=RateLimitPolicy(
                        self._settings.model_rate_limit,
                        self._settings.model_rate_window_s,
                    ),
                )
        except Exception as exc:
            self.stats.errors += 1
            # The exception *type* is safe to log and is usually the whole diagnosis;
            # a constant here once made a permission error indistinguishable from a
            # network blip. The message is not logged: httpx embeds the URL, which
            # carries the bot token (class C8), and database errors echo bound
            # parameters, which carry health values (C2).
            log.warning(
                "telegram update failed",
                integration="telegram",
                outcome="failed",
                reason_code=type(exc).__name__,
            )

        if isinstance(update_id, int):
            self._offset = update_id + 1

        self.stats.outcomes[outcome.value] = self.stats.outcomes.get(outcome.value, 0) + 1
        match outcome:
            case UpdateOutcome.PROCESSED:
                self.stats.processed += 1
            case UpdateOutcome.DUPLICATE:
                self.stats.duplicates += 1
            case UpdateOutcome.REJECTED_CHAT:
                self.stats.rejected += 1
            case _:
                pass
        return outcome


def run(
    settings: Settings | None = None,
    *,
    telegram_secrets: TelegramSecrets | None = None,
    stop_event: threading.Event | None = None,
) -> None:
    """Entry point used by the worker."""
    settings = settings or get_settings()
    telegram_secrets = telegram_secrets or TelegramSecrets(
        bot_token=settings.telegram_bot_token,
        webhook_secret=settings.telegram_webhook_secret,
    )
    if not telegram_secrets.configured_for(settings):
        log.info(
            "telegram not configured; poller idle",
            integration="telegram",
            reason_code="not_configured",
        )
        (stop_event or threading.Event()).wait()
        return

    poller = TelegramPoller(settings, token=telegram_secrets.bot_token, stop_event=stop_event)
    poller.run_forever()


def _sleep(seconds: float) -> None:  # pragma: no cover -- kept for readability in tests
    time.sleep(seconds)
