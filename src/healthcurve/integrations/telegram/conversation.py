"""Bounded, owner/chat-scoped Telegram conversation memory.

The stored window exists only to correlate a short reply with a recent bot question.
It is not a source of facts, a plan, or an instruction to a deterministic command.
Every read validates the JSON boundary and deletes stale or malformed context.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from typing import Any, Literal, cast

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import delete, select
from sqlalchemy.engine import CursorResult
from sqlalchemy.orm import Session

from healthcurve.ai.models import TelegramConversationContext
from healthcurve.config import Settings, get_settings


class ConversationTurn(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    role: Literal["user", "assistant"]
    content: str = Field(min_length=1, max_length=1_000)
    at: datetime


class PendingIntent(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)

    kind: Literal["beads_add"]
    request: str = Field(min_length=8, max_length=500)
    question: str = Field(min_length=8, max_length=300)


def active_context(
    session: Session,
    *,
    owner_id: uuid.UUID,
    chat_id: int,
    now: datetime,
) -> TelegramConversationContext | None:
    """Return valid unexpired context; invalid or expired state is removed."""
    row = session.scalar(
        select(TelegramConversationContext).where(
            TelegramConversationContext.owner_id == owner_id,
            TelegramConversationContext.chat_id == chat_id,
        )
    )
    if row is None:
        return None
    if row.expires_at <= now:
        session.delete(row)
        session.flush()
        return None
    try:
        [ConversationTurn.model_validate(turn) for turn in row.turns]
        if row.pending_intent is not None:
            PendingIntent.model_validate(row.pending_intent)
    except ValidationError:
        session.delete(row)
        session.flush()
        return None
    return row


def pending_intent(
    session: Session, *, owner_id: uuid.UUID, chat_id: int, now: datetime
) -> PendingIntent | None:
    row = active_context(session, owner_id=owner_id, chat_id=chat_id, now=now)
    if row is None or row.pending_intent is None:
        return None
    return PendingIntent.model_validate(row.pending_intent)


def remember_exchange(
    session: Session,
    *,
    owner_id: uuid.UUID,
    chat_id: int,
    user_text: str,
    assistant_text: str,
    now: datetime,
    pending: PendingIntent | None = None,
    settings: Settings | None = None,
) -> TelegramConversationContext:
    """Append one exchange and enforce time, turn-count, and character bounds."""
    configured = settings or get_settings()
    row = active_context(session, owner_id=owner_id, chat_id=chat_id, now=now)
    if row is None:
        row = TelegramConversationContext(
            owner_id=owner_id,
            chat_id=chat_id,
            turns=[],
            expires_at=now + timedelta(minutes=configured.telegram_context_ttl_minutes),
        )
        session.add(row)

    new_turns = [
        *row.turns,
        ConversationTurn(role="user", content=_bounded(user_text), at=now).model_dump(mode="json"),
        ConversationTurn(role="assistant", content=_bounded(assistant_text), at=now).model_dump(
            mode="json"
        ),
    ]
    row.turns = _trim(
        new_turns,
        max_turns=configured.telegram_context_max_turns,
        max_chars=configured.telegram_context_max_chars,
    )
    row.pending_intent = pending.model_dump(mode="json") if pending is not None else None
    row.updated_at = now
    row.expires_at = now + timedelta(minutes=configured.telegram_context_ttl_minutes)
    session.flush()
    return row


def clear_context(session: Session, *, owner_id: uuid.UUID, chat_id: int) -> bool:
    result = cast(
        CursorResult[Any],
        session.execute(
            delete(TelegramConversationContext).where(
                TelegramConversationContext.owner_id == owner_id,
                TelegramConversationContext.chat_id == chat_id,
            )
        ),
    )
    return bool(result.rowcount)


def expire_contexts(session: Session, *, now: datetime | None = None) -> int:
    measured_at = now or datetime.now(UTC)
    result = cast(
        CursorResult[Any],
        session.execute(
            delete(TelegramConversationContext).where(
                TelegramConversationContext.expires_at <= measured_at
            )
        ),
    )
    return int(result.rowcount or 0)


def _bounded(text: str) -> str:
    cleaned = text.strip()
    return cleaned[:1_000] or "(empty)"


def _trim(
    turns: list[dict[str, object]], *, max_turns: int, max_chars: int
) -> list[dict[str, object]]:
    kept = turns[-max_turns:]
    while kept and sum(len(str(turn.get("content", ""))) for turn in kept) > max_chars:
        kept.pop(0)
    return kept
