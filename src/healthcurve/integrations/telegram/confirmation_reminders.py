"""One-time reminders for unanswered Telegram confirmation drafts."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from typing import Final

from sqlalchemy.orm import Session

from healthcurve.ai.models import ExtractionDraft
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.logging import get_logger
from healthcurve.operations.jobs import Job, JobQueueError, enqueue
from healthcurve.operations.worker import JobHandler

CONFIRMATION_REMINDER_DELAY: Final = timedelta(minutes=1)
CONFIRMATION_REMINDER_TASK: Final = "telegram.confirmation_reminders.send"
REMINDER_TEXT: Final = (
    "Reminder: your previous entry is still waiting for confirmation. "
    "Nothing has been recorded yet. Please return to the confirmation message "
    "and use one of its available buttons."
)
log = get_logger(__name__)


def schedule_confirmation_reminder(
    session: Session,
    *,
    draft_id: uuid.UUID,
    confirmation_sent_at: datetime,
) -> Job:
    """Schedule one privacy-safe reminder one minute after a confirmation is sent."""
    if confirmation_sent_at.tzinfo is None or confirmation_sent_at.utcoffset() is None:
        raise JobQueueError("confirmation_reminder_schedule_invalid")
    return enqueue(
        session,
        task=CONFIRMATION_REMINDER_TASK,
        payload={"draft_id": str(draft_id)},
        idempotency_key=f"draft:{draft_id}",
        run_at=confirmation_sent_at.astimezone(UTC) + CONFIRMATION_REMINDER_DELAY,
        priority=45,
        max_attempts=4,
    )


def send_confirmation_reminder(
    session: Session,
    payload: Mapping[str, object],
    *,
    client: TelegramClient,
    chat_id: int,
) -> bool:
    """Send the reminder only while its draft is still awaiting a decision."""
    raw_draft_id = payload.get("draft_id")
    try:
        draft_id = uuid.UUID(raw_draft_id) if isinstance(raw_draft_id, str) else None
    except ValueError as exc:
        raise JobQueueError("confirmation_reminder_payload_invalid") from exc
    if draft_id is None:
        raise JobQueueError("confirmation_reminder_payload_invalid")

    draft = session.get(ExtractionDraft, draft_id)
    if draft is None or not draft.is_pending:
        return False
    if not client.send_message(chat_id, REMINDER_TEXT):
        raise JobQueueError("telegram_confirmation_reminder_send_failed")
    log.info(
        "telegram confirmation reminder sent",
        task=CONFIRMATION_REMINDER_TASK,
        outcome="sent",
    )
    return True


def make_confirmation_reminder_handler(
    *,
    client: TelegramClient,
    chat_id: int,
) -> JobHandler:
    """Build the durable queue handler."""

    def handle(session: Session, payload: Mapping[str, object]) -> None:
        send_confirmation_reminder(session, payload, client=client, chat_id=chat_id)

    return handle
