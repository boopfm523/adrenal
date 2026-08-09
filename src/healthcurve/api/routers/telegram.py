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
import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Header, HTTPException, Request, status
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from healthcurve.api.deps import DbSession
from healthcurve.config import get_settings
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import handlers
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.models import TelegramUpdate
from healthcurve.logging import get_logger

router = APIRouter(prefix="/integrations/telegram", tags=["telegram"])
log = get_logger(__name__)

#: Telegram gives up on an update after repeated non-200s, so we answer 200 for
#: anything we deliberately ignore -- otherwise it would retry a rejected message
#: forever.
_ACK: dict[str, bool] = {"ok": True}

MAX_MESSAGE_LENGTH = 4000


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

    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return _ACK

    message = update.get("message") or {}
    callback = update.get("callback_query") or {}
    raw_chat_id = (message.get("chat") or {}).get("id") or (
        (callback.get("message") or {}).get("chat") or {}
    ).get("id")
    # Untrusted JSON: narrow before it is used as an identifier anywhere.
    chat_id = raw_chat_id if isinstance(raw_chat_id, int) else None

    if chat_id is None or chat_id != settings.telegram_allowed_chat_id:
        _record_update(session, update_id, chat_id or 0, "rejected_chat")
        log.warning(
            "telegram update from unknown chat",
            integration="telegram",
            reason_code="chat_not_allowed",
        )
        return _ACK

    # Replay protection. The unique constraint is the real guard; losing the race just
    # means the second request finds the row already there.
    if not _record_update(session, update_id, chat_id, "processing"):
        log.info("telegram duplicate update ignored", integration="telegram", outcome="duplicate")
        return _ACK

    owner = session.scalar(select(Owner).limit(1))
    if owner is None:
        return _ACK

    client = TelegramClient(settings)

    if callback:
        _handle_callback(session, owner, callback, client)
        return _ACK

    text = message.get("text")
    if not isinstance(text, str):
        client.send_message(chat_id, "I can only read text messages.")
        return _ACK
    if len(text) > MAX_MESSAGE_LENGTH:
        client.send_message(chat_id, "That message is too long for me to read.")
        return _ACK

    reply = handlers.handle_message(
        session, owner, text=text, message_id=str(message.get("message_id"))
    )
    client.send_message(chat_id, reply.text, reply_markup=reply.reply_markup)
    return _ACK


def _handle_callback(
    session: DbSession,
    owner: Owner,
    callback: dict[str, Any],
    client: TelegramClient,
) -> None:
    """Handle a Confirm / Cancel button press."""
    data = callback.get("data") or ""
    query_id = callback.get("id")
    chat_id = ((callback.get("message") or {}).get("chat") or {}).get("id")
    message_id = (callback.get("message") or {}).get("message_id")

    action, _, raw_id = data.partition(":")
    try:
        draft_id = uuid.UUID(raw_id)
    except ValueError:
        if query_id:
            client.answer_callback_query(query_id, "That button has expired.")
        return

    match action:
        case "confirm":
            reply = handlers.confirm_draft(session, owner, draft_id)
        case "cancel":
            reply = handlers.cancel_draft(session, owner, draft_id)
        case _:
            if query_id:
                client.answer_callback_query(query_id, "Unknown action.")
            return

    if query_id:
        client.answer_callback_query(query_id)
    if chat_id and message_id:
        # Replace the buttons so a draft cannot be confirmed twice by tapping again.
        client.edit_message_text(chat_id, message_id, reply.text)
    elif chat_id:
        client.send_message(chat_id, reply.text)


def _record_update(session: DbSession, update_id: int, chat_id: int, outcome: str) -> bool:
    """Claim an update ID. False if it was already seen."""
    existing = session.scalar(
        select(TelegramUpdate.id).where(TelegramUpdate.update_id == update_id)
    )
    if existing is not None:
        return False
    session.add(TelegramUpdate(update_id=update_id, chat_id=chat_id, outcome=outcome))
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        return False
    return True
