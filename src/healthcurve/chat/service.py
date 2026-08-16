"""Conversation lifecycle and bounded context selection for HealthCurve Chat."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from healthcurve.chat.models import (
    ChatConversation,
    ChatMessage,
    ChatMessageState,
    ChatRole,
)

MAX_CONTEXT_TURNS = 12
MAX_CONTEXT_CHARS = 24_000
MAX_SUMMARY_CHARS = 3_000


@dataclass(frozen=True, slots=True)
class ContextTurn:
    role: ChatRole
    body: str
    sequence: int


@dataclass(frozen=True, slots=True)
class BoundedConversationContext:
    summary: str | None
    turns: tuple[ContextTurn, ...]
    character_count: int


def create_conversation(
    session: Session,
    *,
    owner_id: uuid.UUID,
    title: str = "New conversation",
    include_sensitive_text: bool = False,
) -> ChatConversation:
    conversation = ChatConversation(
        owner_id=owner_id,
        title=title.strip(),
        include_sensitive_text=include_sensitive_text,
    )
    session.add(conversation)
    session.flush()
    return conversation


def get_owned_conversation(
    session: Session, *, owner_id: uuid.UUID, conversation_id: uuid.UUID, for_update: bool = False
) -> ChatConversation | None:
    query = select(ChatConversation).where(
        ChatConversation.id == conversation_id,
        ChatConversation.owner_id == owner_id,
    )
    if for_update:
        query = query.with_for_update()
    return session.scalar(query)


def list_conversations(
    session: Session, *, owner_id: uuid.UUID, offset: int, limit: int
) -> tuple[list[ChatConversation], int]:
    predicate = ChatConversation.owner_id == owner_id
    total = session.scalar(select(func.count()).select_from(ChatConversation).where(predicate)) or 0
    rows = list(
        session.scalars(
            select(ChatConversation)
            .where(predicate)
            .order_by(
                ChatConversation.last_message_at.desc().nullslast(),
                ChatConversation.created_at.desc(),
                ChatConversation.id.asc(),
            )
            .offset(offset)
            .limit(limit)
        )
    )
    return rows, total


def update_conversation(
    conversation: ChatConversation,
    *,
    title: str | None = None,
    include_sensitive_text: bool | None = None,
) -> ChatConversation:
    if title is not None:
        conversation.title = title.strip()
    if include_sensitive_text is not None:
        conversation.include_sensitive_text = include_sensitive_text
    conversation.updated_at = datetime.now(UTC)
    return conversation


def delete_conversation(session: Session, conversation: ChatConversation) -> None:
    session.delete(conversation)
    session.flush()


def delete_all_conversations(session: Session, *, owner_id: uuid.UUID) -> int:
    deleted_count = (
        session.scalar(
            select(func.count())
            .select_from(ChatConversation)
            .where(ChatConversation.owner_id == owner_id)
        )
        or 0
    )
    session.execute(delete(ChatConversation).where(ChatConversation.owner_id == owner_id))
    session.flush()
    return deleted_count


def append_user_message(
    session: Session,
    *,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID,
    body: str,
    client_message_id: str,
) -> tuple[ChatMessage, bool]:
    """Append one owner-authored turn, idempotently by browser message identity."""
    conversation = get_owned_conversation(
        session,
        owner_id=owner_id,
        conversation_id=conversation_id,
        for_update=True,
    )
    if conversation is None:
        raise LookupError("conversation not found")

    existing = session.scalar(
        select(ChatMessage).where(
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.owner_id == owner_id,
            ChatMessage.client_message_id == client_message_id,
        )
    )
    if existing is not None:
        if existing.body != body.strip():
            raise ValueError("client message identity reused with different content")
        return existing, False

    last_sequence = (
        session.scalar(
            select(func.max(ChatMessage.sequence)).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.owner_id == owner_id,
            )
        )
        or 0
    )
    now = datetime.now(UTC)
    message = ChatMessage(
        conversation_id=conversation_id,
        owner_id=owner_id,
        role=ChatRole.USER,
        state=ChatMessageState.ACCEPTED,
        body=body.strip(),
        sequence=last_sequence + 1,
        client_message_id=client_message_id,
        created_at=now,
        updated_at=now,
    )
    session.add(message)
    conversation.last_message_at = now
    conversation.updated_at = now
    session.flush()
    return message, True


def queue_assistant_message(
    session: Session,
    *,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID,
    user_message: ChatMessage,
) -> tuple[ChatMessage, bool]:
    """Create the durable response placeholder paired with one accepted user turn."""
    conversation = get_owned_conversation(
        session,
        owner_id=owner_id,
        conversation_id=conversation_id,
        for_update=True,
    )
    if conversation is None or user_message.owner_id != owner_id:
        raise LookupError("conversation not found")
    if user_message.conversation_id != conversation_id or user_message.role is not ChatRole.USER:
        raise ValueError("assistant source message is invalid")

    existing = session.scalar(
        select(ChatMessage).where(
            ChatMessage.owner_id == owner_id,
            ChatMessage.conversation_id == conversation_id,
            ChatMessage.sequence == user_message.sequence + 1,
            ChatMessage.role == ChatRole.ASSISTANT,
        )
    )
    if existing is not None:
        return existing, False

    last_sequence = (
        session.scalar(
            select(func.max(ChatMessage.sequence)).where(
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.owner_id == owner_id,
            )
        )
        or 0
    )
    if last_sequence != user_message.sequence:
        raise ValueError("assistant source message is not the latest turn")

    now = datetime.now(UTC)
    message = ChatMessage(
        conversation_id=conversation_id,
        owner_id=owner_id,
        role=ChatRole.ASSISTANT,
        state=ChatMessageState.QUEUED,
        sequence=user_message.sequence + 1,
        created_at=now,
        updated_at=now,
    )
    session.add(message)
    conversation.last_message_at = now
    conversation.updated_at = now
    session.flush()
    return message, True


def get_owned_message(
    session: Session,
    *,
    owner_id: uuid.UUID,
    message_id: uuid.UUID,
    for_update: bool = False,
) -> ChatMessage | None:
    query = select(ChatMessage).where(
        ChatMessage.id == message_id,
        ChatMessage.owner_id == owner_id,
    )
    if for_update:
        query = query.with_for_update()
    return session.scalar(query)


def list_messages(
    session: Session,
    *,
    owner_id: uuid.UUID,
    conversation_id: uuid.UUID,
    offset: int,
    limit: int,
) -> tuple[list[ChatMessage], int]:
    predicate = (ChatMessage.owner_id == owner_id) & (
        ChatMessage.conversation_id == conversation_id
    )
    total = session.scalar(select(func.count()).select_from(ChatMessage).where(predicate)) or 0
    rows = list(
        session.scalars(
            select(ChatMessage)
            .where(predicate)
            .order_by(ChatMessage.sequence.asc(), ChatMessage.id.asc())
            .offset(offset)
            .limit(limit)
        )
    )
    return rows, total


def bounded_context(
    session: Session, *, owner_id: uuid.UUID, conversation_id: uuid.UUID
) -> BoundedConversationContext:
    conversation = get_owned_conversation(
        session, owner_id=owner_id, conversation_id=conversation_id
    )
    if conversation is None:
        raise LookupError("conversation not found")

    summary = conversation.rolling_summary
    if summary is not None and len(summary) > MAX_SUMMARY_CHARS:
        summary = None

    eligible = (
        (ChatMessage.role == ChatRole.USER) & (ChatMessage.state == ChatMessageState.ACCEPTED)
    ) | (
        (ChatMessage.role == ChatRole.ASSISTANT) & (ChatMessage.state == ChatMessageState.COMPLETED)
    )
    newest = list(
        session.scalars(
            select(ChatMessage)
            .where(
                ChatMessage.owner_id == owner_id,
                ChatMessage.conversation_id == conversation_id,
                ChatMessage.body.is_not(None),
                eligible,
            )
            .order_by(ChatMessage.sequence.desc())
            .limit(MAX_CONTEXT_TURNS)
        )
    )

    selected: list[ContextTurn] = []
    character_count = 0
    for message in newest:
        assert message.body is not None
        remaining = MAX_CONTEXT_CHARS - character_count
        if remaining <= 0:
            break
        body = message.body
        if len(body) > remaining:
            if selected:
                break
            body = body[:remaining]
        selected.append(ContextTurn(role=message.role, body=body, sequence=message.sequence))
        character_count += len(body)

    selected.reverse()
    return BoundedConversationContext(
        summary=summary,
        turns=tuple(selected),
        character_count=character_count,
    )
