"""Durable private-Ollama response jobs for HealthCurve Chat."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from healthcurve.ai.ollama import OllamaClient
from healthcurve.chat import orchestration, service
from healthcurve.chat.models import (
    ChatConversation,
    ChatMessage,
    ChatMessageState,
    ChatRole,
    ChatToolExecution,
    ChatToolOutcome,
)
from healthcurve.chat.tools import ChatToolResult, execute_chat_tool
from healthcurve.identity.models import Owner
from healthcurve.operations import audit
from healthcurve.operations.audit import AuditAction
from healthcurve.operations.jobs import Job, JobQueueError, enqueue
from healthcurve.operations.worker import JobHandler

CHAT_RESPONSE_TASK = "ai.chat.respond"
_TERMINAL_STATES = frozenset(
    {
        ChatMessageState.COMPLETED,
        ChatMessageState.CANCELLED,
        ChatMessageState.UNAVAILABLE,
        ChatMessageState.TIMED_OUT,
        ChatMessageState.INVALID,
        ChatMessageState.FAILED,
    }
)


class _ChatCancelled(RuntimeError):
    pass


@dataclass(frozen=True, slots=True)
class ChatSourceStatus:
    status: Literal["fresh", "stale", "unavailable", "not_applicable"]
    stale: bool | None
    checked_at: datetime


def check_source_staleness(
    factory: sessionmaker[Session],
    *,
    owner_id: uuid.UUID,
    assistant_message_id: uuid.UUID,
) -> ChatSourceStatus:
    """Re-run prior bounded reads and compare their deterministic fingerprints."""
    checked_at = datetime.now(UTC)
    with factory() as metadata_session:
        assistant = service.get_owned_message(
            metadata_session,
            owner_id=owner_id,
            message_id=assistant_message_id,
        )
        if (
            assistant is None
            or assistant.role is not ChatRole.ASSISTANT
            or assistant.state is not ChatMessageState.COMPLETED
        ):
            return ChatSourceStatus(status="not_applicable", stale=None, checked_at=checked_at)
        conversation = metadata_session.get(ChatConversation, assistant.conversation_id)
        if conversation is None or conversation.owner_id != owner_id:
            return ChatSourceStatus(status="not_applicable", stale=None, checked_at=checked_at)
        include_sensitive = conversation.include_sensitive_text
        executions = list(
            metadata_session.scalars(
                select(ChatToolExecution)
                .where(
                    ChatToolExecution.owner_id == owner_id,
                    ChatToolExecution.assistant_message_id == assistant_message_id,
                    ChatToolExecution.outcome == ChatToolOutcome.COMPLETED,
                )
                .order_by(ChatToolExecution.created_at.asc(), ChatToolExecution.id.asc())
            )
        )

    for execution in executions:
        try:
            with factory() as tool_session, tool_session.begin():
                current = execute_chat_tool(
                    tool_session,
                    owner_id=owner_id,
                    tool_name=execution.tool_name,
                    arguments=execution.validated_arguments,
                    allow_sensitive_text=include_sensitive,
                )
        except Exception:
            return ChatSourceStatus(status="unavailable", stale=None, checked_at=checked_at)
        if (
            current.tool_version != execution.tool_version
            or current.result_sha256 != execution.result_fingerprint
        ):
            return ChatSourceStatus(status="stale", stale=True, checked_at=checked_at)
    return ChatSourceStatus(status="fresh", stale=False, checked_at=checked_at)


def enqueue_chat_response(session: Session, assistant: ChatMessage) -> Job:
    """Queue only an opaque assistant-message identifier in operational storage."""
    if assistant.role is not ChatRole.ASSISTANT or assistant.state is not ChatMessageState.QUEUED:
        raise JobQueueError("chat_message_not_queued")
    return enqueue(
        session,
        task=CHAT_RESPONSE_TASK,
        payload={"assistant_message_id": str(assistant.id)},
        idempotency_key=f"assistant:{assistant.id}",
        priority=20,
        max_attempts=2,
    )


def _message_id(payload: Mapping[str, object]) -> uuid.UUID:
    if set(payload) != {"assistant_message_id"}:
        raise JobQueueError("chat_job_payload_invalid")
    try:
        return uuid.UUID(str(payload["assistant_message_id"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise JobQueueError("chat_job_payload_invalid") from exc


def make_chat_response_handler(
    factory: sessionmaker[Session],
    *,
    identity_factory: sessionmaker[Session],
    client: OllamaClient,
) -> JobHandler:
    """Build a handler with restricted AI writes and a bounded identity lookup.

    ``healthcurve_ai`` must remain unable to read the identity schema.  The owner
    timezone is therefore read through the ordinary application role, while all
    chat state and generated output continue to use the restricted AI role.
    """

    def handle(queue_session: Session, payload: Mapping[str, object]) -> None:
        message_id = _message_id(payload)
        with factory() as session:
            assistant = session.get(ChatMessage, message_id)
            if assistant is None or assistant.role is not ChatRole.ASSISTANT:
                raise JobQueueError("chat_message_missing")
            owner_id = assistant.owner_id
            conversation_id = assistant.conversation_id
            source = session.scalar(
                select(ChatMessage).where(
                    ChatMessage.owner_id == owner_id,
                    ChatMessage.conversation_id == conversation_id,
                    ChatMessage.sequence == assistant.sequence - 1,
                    ChatMessage.role == ChatRole.USER,
                    ChatMessage.state == ChatMessageState.ACCEPTED,
                )
            )
            conversation = session.get(ChatConversation, conversation_id)
            if source is None or source.body is None or conversation is None:
                raise JobQueueError("chat_source_missing")
            question = source.body
            include_sensitive = conversation.include_sensitive_text
            context = service.bounded_context(
                session,
                owner_id=owner_id,
                conversation_id=conversation_id,
            )

        with identity_factory() as identity_session:
            owner = identity_session.get(Owner, owner_id)
            if owner is None:
                raise JobQueueError("chat_owner_missing")
            default_timezone = owner.default_timezone
        current_local_date = datetime.now(ZoneInfo(default_timezone)).date()

        def observe_state(state: ChatMessageState) -> None:
            with factory() as state_session, state_session.begin():
                row = service.get_owned_message(
                    state_session,
                    owner_id=owner_id,
                    message_id=message_id,
                    for_update=True,
                )
                if row is None:
                    raise JobQueueError("chat_message_missing")
                if row.state is ChatMessageState.CANCELLED:
                    raise _ChatCancelled
                if row.state in _TERMINAL_STATES:
                    raise JobQueueError("chat_message_terminal")
                row.state = state
                row.updated_at = datetime.now(UTC)

        def run_tool(tool_name: str, arguments: dict[str, object]) -> ChatToolResult:
            with factory() as tool_session, tool_session.begin():
                return execute_chat_tool(
                    tool_session,
                    owner_id=owner_id,
                    tool_name=tool_name,
                    arguments=arguments,
                    allow_sensitive_text=include_sensitive,
                )

        def observe_tool(execution: orchestration.ExecutedTool) -> None:
            with factory() as tool_session, tool_session.begin():
                tool_session.add(
                    ChatToolExecution(
                        conversation_id=conversation_id,
                        assistant_message_id=message_id,
                        owner_id=owner_id,
                        tool_name=execution.result.tool_name,
                        tool_version=execution.result.tool_version,
                        validated_arguments=execution.arguments,
                        outcome=ChatToolOutcome.COMPLETED,
                        duration_ms=execution.duration_ms,
                        result_fingerprint=execution.result.result_sha256,
                        source_manifest=[{"sources": execution.result.source_manifest}],
                    )
                )

        try:
            result = orchestration.run(
                question=question,
                context=context,
                execute_tool=run_tool,
                client=client,
                observe_state=observe_state,
                observe_tool=observe_tool,
                current_local_date=current_local_date,
                default_timezone=default_timezone,
            )
        except _ChatCancelled:
            return
        except Exception:
            # A worker defect must never leave the browser polling an apparently
            # active response forever.  Persist only a stable safe code; the queue
            # still records and retries the operational failure without health text.
            with factory() as failure_session, failure_session.begin():
                failed = failure_session.get(ChatMessage, message_id)
                if failed is not None and failed.state not in _TERMINAL_STATES:
                    failed.state = ChatMessageState.FAILED
                    failed.error_code = "chat_worker_failed"
                    failed.updated_at = datetime.now(UTC)
            raise

        completed_at = datetime.now(UTC)
        with factory() as result_session, result_session.begin():
            assistant = service.get_owned_message(
                result_session,
                owner_id=owner_id,
                message_id=message_id,
                for_update=True,
            )
            if assistant is None:
                raise JobQueueError("chat_message_missing")
            if assistant.state is ChatMessageState.CANCELLED:
                return
            assistant.state = result.state
            assistant.error_code = result.error_code
            assistant.updated_at = completed_at
            if result.state is ChatMessageState.COMPLETED:
                assistant.body = result.body
                assistant.generated_at = completed_at
                assistant.model_name = result.model_name
                assistant.model_digest = result.model_digest
                assistant.prompt_version = orchestration.PROMPT_VERSION
                assistant.schema_version = orchestration.SCHEMA_VERSION
                assistant.tool_versions = result.tool_versions or {}
                assistant.source_manifest = result.source_manifest or []
                assistant.source_scope = result.source_scope or {}
                assistant.source_fingerprint = result.source_fingerprint

        if result.state is ChatMessageState.COMPLETED:
            audit.record(
                queue_session,
                actor=audit.actor_for_owner(owner_id),
                action=AuditAction.CHAT_RESPONSE_GENERATED,
                target_type="chat_message",
                target_id=message_id,
                change_summary="generated private chat response; content omitted",
            )

    return handle
