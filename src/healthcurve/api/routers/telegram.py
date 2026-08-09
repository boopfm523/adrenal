"""Telegram webhook.

This is the only unauthenticated endpoint that can cause a write, so it is also the
most defended (threat model T4). In order, before anything is parsed:

1. Telegram must be fully configured. If not, 404 -- an unconfigured bot has no
   endpoint to probe.
2. The secret token must match, compared in constant time.
3. The chat must be the allow-listed one.
4. The update ID must be new; a redelivery is a no-op.

Even if all four were bypassed, the worst outcome is a *draft*: nothing here writes a
fact without the owner pressing Confirm (SAFE-11, SAFE-12).
"""

from __future__ import annotations

import hmac
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status

from healthcurve.api.deps import DbSession
from healthcurve.config import get_settings
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.dispatch import process_update
from healthcurve.logging import get_logger

router = APIRouter(prefix="/integrations/telegram", tags=["telegram"])
log = get_logger(__name__)

#: Telegram gives up on an update after repeated non-200s, so we answer 200 for
#: anything we deliberately ignore -- otherwise it would retry a rejected message
#: forever.
_ACK: dict[str, bool] = {"ok": True}


@router.post("/webhook", include_in_schema=False)
async def webhook(
    request: Request,
    session: DbSession,
    x_telegram_bot_api_secret_token: Annotated[str | None, Header()] = None,
) -> dict[str, bool]:
    settings = get_settings()

    if not settings.telegram_configured:
        # Not "misconfigured" -- simply not a route that exists here.
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND)

    secret = settings.telegram_webhook_secret
    assert secret is not None  # guaranteed by telegram_configured
    if not x_telegram_bot_api_secret_token or not hmac.compare_digest(
        x_telegram_bot_api_secret_token, secret.get_secret_value()
    ):
        log.warning("telegram webhook rejected", integration="telegram", reason_code="bad_secret")
        # 403 without detail: a forger learns nothing about why.
        raise HTTPException(status_code=status.HTTP_403_FORBIDDEN)

    raw = await request.body()
    if len(raw) > 64_000:
        log.warning("telegram webhook rejected", integration="telegram", reason_code="oversized")
        return _ACK

    try:
        update: dict[str, Any] = await request.json()
    except ValueError:
        log.warning("telegram webhook rejected", integration="telegram", reason_code="bad_json")
        return _ACK

    allowed_chat_id = settings.telegram_allowed_chat_id
    assert allowed_chat_id is not None  # guaranteed by telegram_configured

    # Everything past this point is shared with the poller, so both transports run
    # identical allow-list, deduplication and draft logic (ADR-0008).
    process_update(
        session,
        update,
        allowed_chat_id=allowed_chat_id,
        client=TelegramClient(settings),
    )
    return _ACK
