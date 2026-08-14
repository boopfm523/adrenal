"""Processing an inbound Telegram update, independent of how it arrived.

Both transports -- the long poller (ADR-0008, the default) and the webhook -- funnel
here. A transport decides only *how* an update arrives; it never decides what happens
to it. The allow-list, the deduplication, and the draft flow are the same either way,
so choosing a transport cannot accidentally choose a weaker set of checks.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthcurve.ai.ollama import OllamaClient
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import dose_reminders, handlers
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.models import TelegramUpdate
from healthcurve.logging import get_logger
from healthcurve.operations.rate_limit import RateLimiter, RateLimitPolicy

log = get_logger(__name__)

#: Telegram's own cap is 4096; leave room for our framing.
MAX_MESSAGE_LENGTH = 4000
MAX_PROVIDER_CLOCK_SKEW = timedelta(minutes=5)


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
    model_client: OllamaClient | None = None,
    limiter: RateLimiter | None = None,
    model_policy: RateLimitPolicy | None = None,
    now: datetime | None = None,
) -> UpdateOutcome:
    """Handle one update. Safe to call twice with the same update.

    Returns an outcome rather than raising, because a single bad update must never
    stop a poll loop or fail a webhook response.
    """
    processing_time = now or datetime.now(UTC)
    update_id = update.get("update_id")
    if not isinstance(update_id, int):
        return UpdateOutcome.IGNORED

    chat_id = chat_id_of(update)
    if chat_id is None or chat_id != allowed_chat_id:
        _claim(
            session,
            update_id,
            chat_id or 0,
            UpdateOutcome.REJECTED_CHAT,
            received_at=processing_time,
        )
        log.warning(
            "telegram update from unknown chat",
            integration="telegram",
            reason_code="chat_not_allowed",
        )
        return UpdateOutcome.REJECTED_CHAT

    # The poller's offset is the primary guard; this catches a replay after a crash
    # that lost the offset, and a webhook redelivery.
    message = update.get("message") or {}
    provider_sent_at = message_sent_at_of(message, processing_time=processing_time)
    message_id = message.get("message_id")
    if not _claim(
        session,
        update_id,
        chat_id,
        UpdateOutcome.PROCESSED,
        received_at=processing_time,
        provider_message_id=(
            message_id if isinstance(message_id, int) and not isinstance(message_id, bool) else None
        ),
        provider_sent_at=provider_sent_at,
    ):
        log.info("telegram duplicate update ignored", integration="telegram", outcome="duplicate")
        return UpdateOutcome.DUPLICATE

    owner = session.scalar(select(Owner).limit(1))
    if owner is None:
        return UpdateOutcome.NO_OWNER

    callback = update.get("callback_query")
    if callback:
        _handle_callback(session, owner, callback, client)
        return UpdateOutcome.PROCESSED

    telegram_location = message.get("location")
    if isinstance(telegram_location, dict):
        if (message.get("chat") or {}).get("type") != "private":
            client.send_message(chat_id, "Location sharing is available only in a private chat.")
            return UpdateOutcome.IGNORED
        reply = handlers.handle_phone_location(
            session,
            owner,
            chat_id=chat_id,
            latitude=telegram_location.get("latitude"),
            longitude=telegram_location.get("longitude"),
        )
        client.send_message(chat_id, reply.text, reply_markup=reply.reply_markup)
        return UpdateOutcome.PROCESSED

    text = message.get("text")
    if not isinstance(text, str):
        client.send_message(chat_id, "I can only read text messages.")
        return UpdateOutcome.IGNORED
    if len(text) > MAX_MESSAGE_LENGTH:
        client.send_message(chat_id, "That message is too long for me to read.")
        return UpdateOutcome.IGNORED

    if text == "Use saved Home area":
        reply = handlers.use_saved_home(session, owner, chat_id=chat_id)
        client.send_message(chat_id, reply.text, reply_markup=reply.reply_markup)
        return UpdateOutcome.PROCESSED
    if text == "No location":
        reply = handlers.decline_location(session, owner, chat_id=chat_id)
        client.send_message(chat_id, reply.text, reply_markup=reply.reply_markup)
        return UpdateOutcome.PROCESSED

    reply = handlers.handle_message(
        session,
        owner,
        text=text,
        chat_id=chat_id,
        message_id=str(message.get("message_id")),
        client=model_client,
        limiter=limiter,
        model_policy=model_policy,
        now=provider_sent_at or processing_time,
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
        target_id = uuid.UUID(raw_id)
    except ValueError:
        if query_id:
            client.answer_callback_query(query_id, "That button has expired.")
        return

    if action.startswith("reminder_"):
        reminder_action = action.removeprefix("reminder_")
        text, markup = dose_reminders.handle_action(
            session, owner, target_id, reminder_action, now=datetime.now(UTC)
        )
        if query_id:
            client.answer_callback_query(query_id)
        if chat_id and message_id:
            client.edit_message_text(chat_id, message_id, text, reply_markup=markup)
        elif chat_id:
            client.send_message(chat_id, text, reply_markup=markup)
        return

    match action:
        case "confirm":
            reply = handlers.confirm_draft(session, owner, target_id)
        case "cancel":
            reply = handlers.cancel_draft(session, owner, target_id)
        case "edit":
            reply = handlers.draft_edit_help(session, owner, target_id)
        case "location":
            chat_type = ((callback.get("message") or {}).get("chat") or {}).get("type")
            if chat_type != "private" or not isinstance(chat_id, int):
                if query_id:
                    client.answer_callback_query(
                        query_id, "Location sharing requires a private chat."
                    )
                return
            reply = handlers.start_location_request(session, owner, target_id, chat_id=chat_id)
            if query_id:
                client.answer_callback_query(query_id)
            client.send_message(chat_id, reply.text, reply_markup=reply.reply_markup)
            return
        case "save_home":
            reply = handlers.save_location_as_home(session, owner, target_id)
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


def message_sent_at_of(message: dict[str, Any], *, processing_time: datetime) -> datetime | None:
    """Return Telegram's trusted send instant, or ``None`` for a safe fallback.

    Telegram documents ``message.date`` as Unix time. Delayed timestamps are valid
    because updates may wait through an outage; malformed values and timestamps more
    than a small clock-skew allowance into the future are not used as health-event
    reference times.
    """
    raw = message.get("date")
    if isinstance(raw, bool) or not isinstance(raw, int):
        return None
    try:
        sent_at = datetime.fromtimestamp(raw, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    if sent_at > processing_time + MAX_PROVIDER_CLOCK_SKEW:
        return None
    return sent_at


def _claim(
    session: Session,
    update_id: int,
    chat_id: int,
    outcome: UpdateOutcome,
    *,
    received_at: datetime,
    provider_message_id: int | None = None,
    provider_sent_at: datetime | None = None,
) -> bool:
    """Record an update id. False if it was already seen."""
    existing = session.scalar(
        select(TelegramUpdate.id).where(TelegramUpdate.update_id == update_id)
    )
    if existing is not None:
        return False
    session.add(
        TelegramUpdate(
            update_id=update_id,
            chat_id=chat_id,
            outcome=outcome.value,
            received_at=received_at,
            provider_message_id=provider_message_id,
            provider_sent_at=provider_sent_at,
            reference_time_source=(
                "telegram_message_date" if provider_sent_at else "processing_time_fallback"
            ),
        )
    )
    try:
        session.flush()
    except IntegrityError:
        # Lost a race; the other writer has it.
        session.rollback()
        return False
    return True
