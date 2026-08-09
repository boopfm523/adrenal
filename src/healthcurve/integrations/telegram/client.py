"""Outbound Telegram Bot API calls.

Kept deliberately small. Sending is best-effort: a failure to deliver a confirmation
message must never roll back a recorded fact, so every method returns a bool instead
of raising.

The bot token is class C8 -- never logged, never exported, never in an error message.
"""

from __future__ import annotations

from typing import Any

import httpx
from pydantic import SecretStr

from healthcurve.config import Settings, get_settings
from healthcurve.logging import get_logger

log = get_logger(__name__)

API_BASE = "https://api.telegram.org"
TIMEOUT = httpx.Timeout(connect=5.0, read=15.0, write=10.0, pool=5.0)


class TelegramClient:
    def __init__(self, settings: Settings | None = None, *, token: SecretStr | None = None) -> None:
        self._settings = settings or get_settings()
        self._token = token or self._settings.telegram_bot_token

    @property
    def configured(self) -> bool:
        return self._token is not None

    def _url(self, method: str) -> str:
        token = self._token
        assert token is not None  # guarded by `configured`
        return f"{API_BASE}/bot{token.get_secret_value()}/{method}"

    def _post(self, method: str, payload: dict[str, Any]) -> dict[str, Any] | None:
        if not self.configured:
            log.warning(
                "telegram send skipped", integration="telegram", reason_code="not_configured"
            )
            return None
        try:
            with httpx.Client(timeout=TIMEOUT) as client:
                response = client.post(self._url(method), json=payload)
                response.raise_for_status()
                return response.json()
        except httpx.HTTPError as exc:
            # Never interpolate the URL: it contains the bot token.
            log.warning(
                "telegram send failed",
                integration="telegram",
                reason_code=type(exc).__name__,
                outcome="failed",
            )
            return None

    def send_message(
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
        reply_to_message_id: int | None = None,
    ) -> bool:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "text": text,
            # No parse_mode: health text is rendered literally, so a stray underscore
            # or asterisk cannot break formatting or inject markup.
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        if reply_to_message_id is not None:
            payload["reply_to_message_id"] = reply_to_message_id
        return self._post("sendMessage", payload) is not None

    def answer_callback_query(self, callback_query_id: str, text: str = "") -> bool:
        return (
            self._post(
                "answerCallbackQuery", {"callback_query_id": callback_query_id, "text": text}
            )
            is not None
        )

    def edit_message_text(
        self,
        chat_id: int | str,
        message_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        payload: dict[str, Any] = {
            "chat_id": chat_id,
            "message_id": message_id,
            "text": text,
            "disable_web_page_preview": True,
        }
        if reply_markup is not None:
            payload["reply_markup"] = reply_markup
        return self._post("editMessageText", payload) is not None

    def set_webhook(self, url: str, secret_token: str) -> dict[str, Any] | None:
        """Register the webhook. Used by the setup command, not at runtime."""
        return self._post(
            "setWebhook",
            {
                "url": url,
                "secret_token": secret_token,
                "allowed_updates": ["message", "callback_query"],
                # Drop anything queued while the webhook was unset, so old messages
                # do not arrive as fresh drafts after a reconnect.
                "drop_pending_updates": True,
            },
        )

    def delete_webhook(self, *, drop_pending_updates: bool = True) -> dict[str, Any] | None:
        """Remove the webhook.

        ``drop_pending_updates`` defaults to True for an explicit disconnect, but the
        poller passes False: queued updates are real messages the owner sent, and the
        offset plus the deduplication table make replaying them safe.
        """
        return self._post("deleteWebhook", {"drop_pending_updates": drop_pending_updates})

    def get_webhook_info(self) -> dict[str, Any] | None:
        return self._post("getWebhookInfo", {})

    def get_me(self) -> dict[str, Any] | None:
        return self._post("getMe", {})
