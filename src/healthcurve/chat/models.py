"""Persistent, owner-scoped working state for the private HealthCurve chatbot.

Chat is stored in the ``ai`` schema because a conversation is working context and
generated interpretation, never a recorded fact or physician-approved plan.  The
foreign keys all cascade *within* chat so the owner can remove a conversation without
touching either of those authoritative categories.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import AI_SCHEMA, AIBase, StrEnumType


class ChatRole(StrEnum):
    USER = "user"
    ASSISTANT = "assistant"


class ChatMessageState(StrEnum):
    ACCEPTED = "accepted"
    QUEUED = "queued"
    PLANNING = "planning"
    READING = "reading"
    GENERATING = "generating"
    COMPLETED = "completed"
    CANCELLED = "cancelled"
    UNAVAILABLE = "unavailable"
    TIMED_OUT = "timed_out"
    INVALID = "invalid"
    FAILED = "failed"


class ChatToolOutcome(StrEnum):
    QUEUED = "queued"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMED_OUT = "timed_out"
    CANCELLED = "cancelled"
    INVALID = "invalid"


class ChatConversation(AIBase):
    __tablename__ = "chat_conversation"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    title: Mapped[str] = mapped_column(
        String(120), nullable=False, default="New conversation", server_default="New conversation"
    )
    include_sensitive_text: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )
    rolling_summary: Mapped[str | None] = mapped_column(Text)
    retention_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )
    last_message_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint("char_length(title) BETWEEN 1 AND 120", name="title_length"),
        CheckConstraint(
            "rolling_summary IS NULL OR char_length(rolling_summary) <= 3000",
            name="summary_length",
        ),
        Index("ix_chat_conversation_owner_recent", "owner_id", "last_message_at", "created_at"),
        AI_SCHEMA,
    )


class ChatMessage(AIBase):
    __tablename__ = "chat_message"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai.chat_conversation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    role: Mapped[ChatRole] = mapped_column(StrEnumType(ChatRole, 16), nullable=False)
    state: Mapped[ChatMessageState] = mapped_column(
        StrEnumType(ChatMessageState, 16), nullable=False
    )
    body: Mapped[str | None] = mapped_column(Text)
    sequence: Mapped[int] = mapped_column(Integer, nullable=False)
    client_message_id: Mapped[str | None] = mapped_column(String(128))

    generated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    model_name: Mapped[str | None] = mapped_column(String(120))
    model_digest: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str | None] = mapped_column(String(32))
    schema_version: Mapped[str | None] = mapped_column(String(32))
    tool_versions: Mapped[dict[str, str] | None] = mapped_column(JSONB)
    source_manifest: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    source_scope: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    source_fingerprint: Mapped[str | None] = mapped_column(String(128))
    error_code: Mapped[str | None] = mapped_column(String(64))

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        UniqueConstraint("conversation_id", "sequence", name="uq_chat_message_sequence"),
        UniqueConstraint("conversation_id", "client_message_id", name="uq_chat_message_client_id"),
        CheckConstraint("sequence > 0", name="sequence_positive"),
        CheckConstraint(
            "role <> 'user' OR (state = 'accepted' AND body IS NOT NULL "
            "AND char_length(body) BETWEEN 1 AND 8000)",
            name="user_message_valid",
        ),
        CheckConstraint(
            "state <> 'completed' OR (role = 'assistant' AND body IS NOT NULL "
            "AND char_length(body) BETWEEN 1 AND 32000)",
            name="completed_assistant_body",
        ),
        CheckConstraint(
            "state <> 'completed' OR (generated_at IS NOT NULL "
            "AND model_name IS NOT NULL AND char_length(model_name) > 0 "
            "AND model_digest IS NOT NULL AND char_length(model_digest) > 0 "
            "AND prompt_version IS NOT NULL AND char_length(prompt_version) > 0 "
            "AND schema_version IS NOT NULL AND char_length(schema_version) > 0 "
            "AND tool_versions IS NOT NULL AND jsonb_typeof(tool_versions) = 'object' "
            "AND source_manifest IS NOT NULL AND jsonb_typeof(source_manifest) = 'array' "
            "AND source_scope IS NOT NULL AND jsonb_typeof(source_scope) = 'object' "
            "AND source_fingerprint IS NOT NULL AND char_length(source_fingerprint) > 0)",
            name="completed_assistant_provenance",
        ),
        Index("ix_chat_message_owner_conversation", "owner_id", "conversation_id", "sequence"),
        AI_SCHEMA,
    )


class ChatToolExecution(AIBase):
    __tablename__ = "chat_tool_execution"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai.chat_conversation.id", ondelete="CASCADE"), nullable=False, index=True
    )
    assistant_message_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ai.chat_message.id", ondelete="CASCADE"), nullable=False, index=True
    )
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    tool_name: Mapped[str] = mapped_column(String(80), nullable=False)
    tool_version: Mapped[str] = mapped_column(String(32), nullable=False)
    validated_arguments: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[ChatToolOutcome] = mapped_column(
        StrEnumType(ChatToolOutcome, 16), nullable=False
    )
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    result_fingerprint: Mapped[str | None] = mapped_column(String(128))
    source_manifest: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    error_code: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("duration_ms IS NULL OR duration_ms >= 0", name="duration_nonnegative"),
        CheckConstraint("jsonb_typeof(validated_arguments) = 'object'", name="arguments_object"),
        Index("ix_chat_tool_message_created", "assistant_message_id", "created_at"),
        AI_SCHEMA,
    )
