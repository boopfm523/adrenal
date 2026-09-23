"""Durable private-Ollama response jobs for HealthCurve Chat (ADR-0036)."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from zoneinfo import ZoneInfo

from sqlalchemy import Engine, select
from sqlalchemy.orm import Session, sessionmaker

from healthcurve.ai.ollama import OllamaClient
from healthcurve.analysis.tools import (
    AnalysisAccess,
    ToolOutput,
    execute_analysis_tool,
    tool_definitions,
)
from healthcurve.chat import orchestration, service
from healthcurve.chat.models import (
    ChatConversation,
    ChatMessage,
    ChatMessageState,
    ChatRole,
    ChatToolExecution,
    ChatToolOutcome,
)
from healthcurve.config import Settings
from healthcurve.identity import timezones
from healthcurve.identity.models import Owner
from healthcurve.logging import get_logger
from healthcurve.operations import audit
from healthcurve.operations.audit import AuditAction
from healthcurve.operations.jobs import Job, JobQueueError, enqueue
from healthcurve.operations.worker import JobHandler

log = get_logger(__name__)

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

#: Builds analysis access for a conversation; the argument is whether text is enabled.
type AccessFactory = Callable[[bool], AnalysisAccess]


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
    access_for: AccessFactory,
) -> ChatSourceStatus:
    """Re-run an answer's successful tool calls and compare their result fingerprints."""
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
        include_text = conversation.include_sensitive_text
        executions = [
            (
                execution.tool_name,
                execution.tool_version,
                execution.result_fingerprint,
                dict(execution.validated_arguments),
            )
            for execution in metadata_session.scalars(
                select(ChatToolExecution)
                .where(
                    ChatToolExecution.owner_id == owner_id,
                    ChatToolExecution.assistant_message_id == assistant_message_id,
                    ChatToolExecution.outcome == ChatToolOutcome.COMPLETED,
                )
                .order_by(ChatToolExecution.created_at.asc(), ChatToolExecution.id.asc())
            )
        ]

    access = access_for(include_text)
    for tool_name, tool_version, fingerprint, arguments in executions:
        try:
            current = execute_analysis_tool(access, tool_name, arguments)
        except Exception:
            return ChatSourceStatus(status="unavailable", stale=None, checked_at=checked_at)
        if not current.ok:
            return ChatSourceStatus(status="unavailable", stale=None, checked_at=checked_at)
        if current.tool_version != tool_version or current.result_sha256 != fingerprint:
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


@dataclass(frozen=True, slots=True)
class _ModelChoice:
    client: OllamaClient
    think: bool
    available: bool


def _choose_model(client: OllamaClient, settings: Settings, model_name: str | None) -> _ModelChoice:
    """Bind the conversation's chosen local model, re-checked when the answer runs.

    The default model keeps its configured behavior. Any other choice must still be an
    installed local tool-calling model, and thinking is requested only if it supports it.
    """
    if model_name is None or model_name == settings.ollama_model:
        return _ModelChoice(client=client, think=settings.chat_thinking, available=True)
    installed = client.chat_models() or ()
    chosen = next((model for model in installed if model.name == model_name), None)
    if chosen is None:
        return _ModelChoice(client=client, think=False, available=False)
    return _ModelChoice(
        client=client.for_model(chosen.name),
        think=settings.chat_thinking and chosen.thinking,
        available=True,
    )


def make_chat_response_handler(
    factory: sessionmaker[Session],
    *,
    identity_factory: sessionmaker[Session],
    client: OllamaClient,
    settings: Settings,
    analyst_engine: Engine | None,
    analyst_text_engine: Engine | None,
) -> JobHandler:
    """Build a handler with restricted AI writes and a bounded identity lookup.

    ``healthcurve_ai`` must remain unable to read the identity schema.  The owner
    timezone is therefore read through the ordinary application role, while all
    chat state and generated output continue to use the restricted AI role. Model
    queries run only on the view-only analyst engines (ADR-0036).
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
            include_text = conversation.include_sensitive_text
            chosen_model = conversation.model_name
            context = service.bounded_context(
                session,
                owner_id=owner_id,
                conversation_id=conversation_id,
            )

        with identity_factory() as identity_session:
            owner = identity_session.get(Owner, owner_id)
            if owner is None:
                raise JobQueueError("chat_owner_missing")
            # "Today" in an answer means the day the asker is living in, which while
            # travelling is not the day at home.
            owner_timezone = timezones.current_zone(identity_session, owner)
        current_local_datetime = datetime.now(ZoneInfo(owner_timezone))

        access = AnalysisAccess(
            engine=analyst_engine,
            text_engine=analyst_text_engine,
            allow_text=include_text,
            owner_id=owner_id,
            timezone=owner_timezone,
            model_session_factory=factory,
        )
        query_engine = analyst_text_engine if include_text else analyst_engine

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

        def run_tool(tool_name: str, arguments: dict[str, Any]) -> ToolOutput:
            try:
                return execute_analysis_tool(access, tool_name, arguments)
            except Exception:
                # A tool defect becomes a repairable result rather than a failed run.
                log.warning("analysis tool failed", reason_code="chat_tool_failed")
                return ToolOutput(
                    tool_name=tool_name,
                    tool_version="unknown",
                    ok=False,
                    error_code="tool_failed",
                    error_message="The tool failed unexpectedly; try a simpler request.",
                )

        def observe_tool(execution: orchestration.ExecutedTool) -> None:
            output = execution.output
            with factory() as tool_session, tool_session.begin():
                tool_session.add(
                    ChatToolExecution(
                        conversation_id=conversation_id,
                        assistant_message_id=message_id,
                        owner_id=owner_id,
                        tool_name=execution.tool_name[:80],
                        tool_version=output.tool_version[:32],
                        validated_arguments=execution.arguments,
                        outcome=(
                            ChatToolOutcome.COMPLETED if output.ok else ChatToolOutcome.INVALID
                        ),
                        duration_ms=execution.duration_ms,
                        result_fingerprint=output.result_sha256 or None,
                        source_manifest=[{"views": list(output.views)}],
                        error_code=None if output.ok else (output.error_code or "tool_failed")[:64],
                    )
                )

        choice = _choose_model(client, settings, chosen_model)
        try:
            if not choice.available:
                result = orchestration.OrchestrationResult(
                    state=ChatMessageState.UNAVAILABLE, error_code="chat_model_not_available"
                )
            else:
                result = orchestration.run(
                    question=question,
                    context=context,
                    tools=tool_definitions(access),
                    execute_tool=run_tool,
                    client=choice.client,
                    current_local_datetime=current_local_datetime,
                    default_timezone=owner_timezone,
                    allow_text=include_text,
                    analysis_configured=query_engine is not None,
                    think=choice.think,
                    context_window=settings.chat_context_window or settings.ollama_context_window,
                    max_output_tokens=settings.chat_max_output_tokens,
                    read_timeout_s=settings.chat_read_timeout_s,
                    observe_state=observe_state,
                    observe_tool=observe_tool,
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
