"""Processing an inbound Telegram update, independent of how it arrived.

Both transports -- the long poller (ADR-0008, the default) and the webhook -- funnel
here. A transport decides only *how* an update arrives; it never decides what happens
to it. The allow-list, the deduplication, and the draft flow are the same either way,
so choosing a transport cannot accidentally choose a weaker set of checks.
"""

from __future__ import annotations

import uuid
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import handlers
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.models import TelegramUpdate
from healthcurve.logging import get_logger

log = get_logger(__name__)

#: Telegram's own cap is 4096; leave room for our framing.
MAX_MESSAGE_LENGTH = 4000


class UpdateOutcome(StrEnum):
    """What happened to an update. Recorded, and used by the poller for logging."""

    PROCESSED = "processed"
    DUPLICATE = "duplicate"
    REJECTED_CHAT = "rejected_chat"
    IGNORED = "ignored"  # not a text message we can act on
    NO_OWNER = "no_owner"


def chat_id_of(update: dict[str, Any]) -> int | None:
    """The chat an update belongs to, narrowed from untrusted JSON."""
    message = update.get("message") or {}
    callback = update.get("callback_query") or {}
    raw = (message.get("chat") or {}).get("id") or (
        (callback.get("message") or {}).get("chat") or {}
    ).get("id")
    return raw if isinstance(raw, int) else None


def process_update(
    session: Session,
    update: dict[str, Any],
    *,
    allowed_chat_id: int,
    client: TelegramClient,
) -> UpdateOutcome:
    """Handle one update. Safe to call twice with the same update.

    Returns an outcome rather than raising, because a single bad update must never
    stop a poll loop or fail a webhook response.
    """
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return UpdateOutcome.IGNORED

    chat_id = chat_id_of(update)
    if chat_id is None or chat_id != allowed_chat_id:
        _claim(session, update_id, chat_id or 0, UpdateOutcome.REJECTED_CHAT)
        log.warning(
            "telegram update from unknown chat",
            integration="telegram",
            reason_code="chat_not_allowed",
        )
        return UpdateOutcome.REJECTED_CHAT

    # The poller's offset is the primary guard; this catches a replay after a crash
    # that lost the offset, and a webhook redelivery.
    if not _claim(session, update_id, chat_id, UpdateOutcome.PROCESSED):
        log.info("telegram duplicate update ignored", integration="telegram", outcome="duplicate")
        return UpdateOutcome.DUPLICATE

    owner = session.scalar(select(Owner).limit(1))
    if owner is None:
        return UpdateOutcome.NO_OWNER

    callback = update.get("callback_query")
    if callback:
        _handle_callback(session, owner, callback, client)
        return UpdateOutcome.PROCESSED

    message = update.get("message") or {}
    text = message.get("text")
    if not isinstance(text, str):
        client.send_message(chat_id, "I can only read text messages.")
        return UpdateOutcome.IGNORED
    if len(text) > MAX_MESSAGE_LENGTH:
        client.send_message(chat_id, "That message is too long for me to read.")
        return UpdateOutcome.IGNORED

    reply = handlers.handle_message(
        session, owner, text=text, message_id=str(message.get("message_id"))
    )
    client.send_message(chat_id, reply.text, reply_markup=reply.reply_markup)
    return UpdateOutcome.PROCESSED


def _handle_callback(
    session: Session,
    owner: Owner,
    callback: dict[str, Any],
    client: TelegramClient,
) -> None:
    """A Confirm, Edit, or Cancel button press."""
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
        case "edit":
            reply = handlers.draft_edit_help(session, owner, draft_id)
        case _:
            if query_id:
                client.answer_callback_query(query_id, "Unknown action.")
            return

    if query_id:
        client.answer_callback_query(query_id)
    if chat_id and message_id:
        # Replace the buttons so a draft cannot be confirmed twice by tapping again.
        client.edit_message_text(chat_id, message_id, reply.text, reply_markup=reply.reply_markup)
    elif chat_id:
        client.send_message(chat_id, reply.text)


def _claim(session: Session, update_id: int, chat_id: int, outcome: UpdateOutcome) -> bool:
    """Record an update id. False if it was already seen."""
    existing = session.scalar(
        select(TelegramUpdate.id).where(TelegramUpdate.update_id == update_id)
    )
    if existing is not None:
        return False
    session.add(TelegramUpdate(update_id=update_id, chat_id=chat_id, outcome=outcome.value))
    try:
        session.flush()
    except IntegrityError:
        # Lost a race; the other writer has it.
        session.rollback()
        return False
    return True
