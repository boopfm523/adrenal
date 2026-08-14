"""AI-namespace tables: extraction drafts and generated analyses.

Everything here is deletable and regenerable without touching a fact or a plan
(SAFE-06), and the AI database role can write only this schema (SAFE-15, SAFE-16).

Drafts hold raw message text, which is class C9 -- health content verbatim. They are
purged when resolved rather than kept, so the retained record is the structured fact
rather than the chat message that produced it.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Index,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import AI_SCHEMA, AIBase, StrEnumType


class DraftState(StrEnum):
    PENDING = "pending"  # shown to the owner, awaiting a decision
    CONFIRMED = "confirmed"  # became one or more facts
    EDITED = "edited"  # owner changed values, then confirmed
    CANCELLED = "cancelled"
    EXPIRED = "expired"  # never answered; purged on schedule


class ExtractionDraft(AIBase):
    """Candidate events awaiting confirmation.

    A draft is never a fact (SAFE-12): it does not appear on the timeline, is not
    counted in any total, and is not exported as a record.
    """

    __tablename__ = "extraction_draft"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    #: Where the text came from: "telegram", "web".
    source: Mapped[str] = mapped_column(String(32), nullable=False)
    #: Provider message identity, so a redelivered update cannot create a second draft.
    provider_message_id: Mapped[str | None] = mapped_column(String(128))

    #: Class C9. Discarded when the draft resolves.
    raw_text: Mapped[str | None] = mapped_column(Text)

    candidates: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False)
    #: First model/command proposal, captured before the owner's first edit. This is
    #: evaluation provenance, never a fact and never used for confirmation.
    original_candidates: Mapped[list[dict[str, object]] | None] = mapped_column(JSONB)
    state: Mapped[DraftState] = mapped_column(
        StrEnumType(DraftState, 16), nullable=False, default=DraftState.PENDING
    )

    # --- Reproducibility (SAFE-05) ---
    model_name: Mapped[str | None] = mapped_column(String(120))
    model_digest: Mapped[str | None] = mapped_column(String(128))
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(32), nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: IDs of the facts this draft became. Traceability without a foreign key, because
    #: the ai schema must never hold a FK into fact (SAFE-01).
    created_event_ids: Mapped[list[str] | None] = mapped_column(JSONB)

    __table_args__ = (
        Index("ix_draft_owner_state", "owner_id", "state"),
        Index("uq_draft_provider_message", "source", "provider_message_id", unique=True),
        AI_SCHEMA,
    )

    @property
    def is_pending(self) -> bool:
        return self.state in {DraftState.PENDING, DraftState.EDITED} and self.resolved_at is None

    def purge_raw_text(self) -> None:
        """Drop the verbatim message once the structured fact exists (C9 retention)."""
        self.raw_text = None


class TelegramConversationContext(AIBase):
    """Bounded, short-lived context for one owner and one Telegram chat.

    This is AI working memory, never a recorded fact or approved plan. ``turns`` and
    ``pending_intent`` are validated by the conversation service on every read; a
    malformed row is deleted instead of being supplied to a model.
    """

    __tablename__ = "telegram_conversation_context"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    turns: Mapped[list[dict[str, object]]] = mapped_column(JSONB, nullable=False, default=list)
    pending_intent: Mapped[dict[str, object] | None] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    __table_args__ = (
        UniqueConstraint("owner_id", "chat_id", name="uq_telegram_context_owner_chat"),
        CheckConstraint("jsonb_typeof(turns) = 'array'", name="turns_array"),
        Index("ix_telegram_context_expiry", "expires_at"),
        AI_SCHEMA,
    )


class AnalysisType(StrEnum):
    DAILY_SUMMARY = "daily_summary"
    EPISODE_SUMMARY = "episode_summary"
    PATTERN_OBSERVATION = "pattern_observation"
    REPORT_NARRATIVE = "report_narrative"


class AIAnalysis(AIBase):
    """A generated summary or observation.

    Every row records what it was generated from. An analysis that cannot cite its
    inputs is not persisted and not shown (SAFE-05).
    """

    __tablename__ = "ai_analysis"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)

    analysis_type: Mapped[AnalysisType] = mapped_column(
        StrEnumType(AnalysisType, 32), nullable=False
    )
    body: Mapped[str] = mapped_column(Text, nullable=False)

    #: The source manifest. Non-null and non-empty by construction.
    source_record_ids: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    range_start: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    range_end: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    #: The deterministic figures the narrative is allowed to mention (SAFE-20).
    computed_inputs: Mapped[dict[str, object] | None] = mapped_column(JSONB)

    model_name: Mapped[str] = mapped_column(String(120), nullable=False)
    model_digest: Mapped[str] = mapped_column(String(128), nullable=False)
    prompt_version: Mapped[str] = mapped_column(String(32), nullable=False)
    schema_version: Mapped[str] = mapped_column(
        String(32), nullable=False, default="analysis-v1", server_default=text("'analysis-v1'")
    )

    generated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    hidden_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        CheckConstraint(
            "jsonb_typeof(source_record_ids) = 'array' "
            "AND jsonb_array_length(source_record_ids) > 0",
            name="source_manifest_nonempty",
        ),
        CheckConstraint("char_length(body) > 0", name="body_nonempty"),
        CheckConstraint("char_length(model_digest) > 0", name="model_digest_nonempty"),
        CheckConstraint("char_length(schema_version) > 0", name="schema_version_nonempty"),
        AI_SCHEMA,
    )
