"""Owner-authenticated conversation and message lifecycle for HealthCurve Chat."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status

from healthcurve.api.chat_schemas import (
    ChatConversationCreate,
    ChatConversationOut,
    ChatConversationPage,
    ChatConversationUpdate,
    ChatMessageOut,
    ChatMessagePage,
    ChatMessageStalenessOut,
    ChatUserMessageCreate,
)
from healthcurve.api.deps import (
    AppAiSessionFactory,
    AppRateLimiter,
    AppSettings,
    CurrentOwner,
    DbSession,
    enforce_rate_limit,
    require_csrf,
)
from healthcurve.api.pagination import Pagination, page_metadata
from healthcurve.chat import service
from healthcurve.chat.jobs import check_source_staleness, enqueue_chat_response
from healthcurve.chat.models import ChatConversation, ChatMessage, ChatMessageState, ChatRole
from healthcurve.operations import audit
from healthcurve.operations.audit import AuditAction
from healthcurve.operations.rate_limit import RateLimitPolicy

router = APIRouter(prefix="/chat", tags=["chat"])


@router.get("/conversations", response_model=ChatConversationPage)
def list_conversations(
    session: DbSession, owner: CurrentOwner, pagination: Pagination
) -> ChatConversationPage:
    rows, total = service.list_conversations(
        session,
        owner_id=owner.id,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    return ChatConversationPage(
        items=[_conversation_out(row) for row in rows],
        page=page_metadata(total, pagination),
    )


@router.post(
    "/conversations",
    response_model=ChatConversationOut,
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_conversation(
    payload: ChatConversationCreate, session: DbSession, owner: CurrentOwner
) -> ChatConversationOut:
    conversation = service.create_conversation(
        session,
        owner_id=owner.id,
        title=payload.title,
        include_sensitive_text=payload.include_sensitive_text,
    )
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=AuditAction.CHAT_CONVERSATION_CREATED,
        target_type="chat_conversation",
        target_id=conversation.id,
        change_summary="created chat conversation; values omitted",
    )
    return _conversation_out(conversation)


@router.delete(
    "/conversations",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_all_conversations(session: DbSession, owner: CurrentOwner) -> Response:
    deleted_count = service.delete_all_conversations(session, owner_id=owner.id)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=AuditAction.CHAT_CONVERSATIONS_DELETED,
        target_type="chat_conversation",
        change_summary=f"deleted {deleted_count} chat conversation(s); values omitted",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.get("/conversations/{conversation_id}", response_model=ChatConversationOut)
def get_conversation(
    conversation_id: uuid.UUID, session: DbSession, owner: CurrentOwner
) -> ChatConversationOut:
    return _conversation_out(_owned_conversation(session, owner.id, conversation_id))


@router.patch(
    "/conversations/{conversation_id}",
    response_model=ChatConversationOut,
    dependencies=[Depends(require_csrf)],
)
def update_conversation(
    conversation_id: uuid.UUID,
    payload: ChatConversationUpdate,
    session: DbSession,
    owner: CurrentOwner,
) -> ChatConversationOut:
    conversation = _owned_conversation(session, owner.id, conversation_id)
    changed_fields = sorted(payload.model_fields_set)
    service.update_conversation(
        conversation,
        title=payload.title,
        include_sensitive_text=payload.include_sensitive_text,
    )
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=AuditAction.CHAT_CONVERSATION_UPDATED,
        target_type="chat_conversation",
        target_id=conversation.id,
        change_summary=f"changed fields: {','.join(changed_fields)}; values omitted",
    )
    return _conversation_out(conversation)


@router.delete(
    "/conversations/{conversation_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_conversation(
    conversation_id: uuid.UUID, session: DbSession, owner: CurrentOwner
) -> Response:
    conversation = _owned_conversation(session, owner.id, conversation_id)
    service.delete_conversation(session, conversation)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=AuditAction.CHAT_CONVERSATION_DELETED,
        target_type="chat_conversation",
        target_id=conversation_id,
        change_summary="deleted chat messages and tool metadata; values omitted",
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT, headers={"Cache-Control": "no-store"})


@router.get(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessagePage,
)
def list_messages(
    conversation_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
    pagination: Pagination,
) -> ChatMessagePage:
    _owned_conversation(session, owner.id, conversation_id)
    rows, total = service.list_messages(
        session,
        owner_id=owner.id,
        conversation_id=conversation_id,
        offset=pagination.offset,
        limit=pagination.page_size,
    )
    return ChatMessagePage(
        items=[_message_out(row) for row in rows],
        page=page_metadata(total, pagination),
    )


@router.post(
    "/conversations/{conversation_id}/messages",
    response_model=ChatMessageOut,
    status_code=status.HTTP_202_ACCEPTED,
    dependencies=[Depends(require_csrf)],
)
def append_message(
    conversation_id: uuid.UUID,
    payload: ChatUserMessageCreate,
    session: DbSession,
    owner: CurrentOwner,
    response: Response,
    limiter: AppRateLimiter,
    settings: AppSettings,
) -> ChatMessageOut:
    enforce_rate_limit(
        response,
        limiter,
        scope="chat_model",
        identity=str(owner.id),
        policy=RateLimitPolicy(settings.model_rate_limit, settings.model_rate_window_s),
    )
    try:
        message, created = service.append_user_message(
            session,
            owner_id=owner.id,
            conversation_id=conversation_id,
            body=payload.body,
            client_message_id=payload.client_message_id,
        )
    except LookupError:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found"
        ) from None
    except ValueError:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "client_message_id_conflict"},
        ) from None
    if created:
        assistant, _ = service.queue_assistant_message(
            session,
            owner_id=owner.id,
            conversation_id=conversation_id,
            user_message=message,
        )
        enqueue_chat_response(session, assistant)
        audit.record(
            session,
            actor=audit.actor_for_owner(owner.id),
            action=AuditAction.CHAT_MESSAGE_ACCEPTED,
            target_type="chat_message",
            target_id=message.id,
            change_summary="accepted owner-authored chat message; content omitted",
        )
    return _message_out(message)


@router.post(
    "/messages/{message_id}/cancel",
    response_model=ChatMessageOut,
    dependencies=[Depends(require_csrf)],
)
def cancel_message(
    message_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
) -> ChatMessageOut:
    message = service.get_owned_message(
        session,
        owner_id=owner.id,
        message_id=message_id,
        for_update=True,
    )
    if message is None or message.role is not ChatRole.ASSISTANT:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="message not found")
    if message.state not in {
        ChatMessageState.QUEUED,
        ChatMessageState.PLANNING,
        ChatMessageState.READING,
        ChatMessageState.GENERATING,
    }:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "message_not_cancellable"},
        )
    message.state = ChatMessageState.CANCELLED
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=AuditAction.CHAT_RESPONSE_CANCELLED,
        target_type="chat_message",
        target_id=message.id,
        change_summary="cancelled private chat response; content omitted",
    )
    return _message_out(message)


@router.get(
    "/messages/{message_id}/staleness",
    response_model=ChatMessageStalenessOut,
)
def get_message_staleness(
    message_id: uuid.UUID,
    session: DbSession,
    owner: CurrentOwner,
    ai_factory: AppAiSessionFactory,
) -> ChatMessageStalenessOut:
    if service.get_owned_message(session, owner_id=owner.id, message_id=message_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="message not found")
    result = check_source_staleness(
        ai_factory,
        owner_id=owner.id,
        assistant_message_id=message_id,
    )
    return ChatMessageStalenessOut(
        status=result.status,
        stale=result.stale,
        checked_at=result.checked_at,
    )


def _owned_conversation(
    session: DbSession, owner_id: uuid.UUID, conversation_id: uuid.UUID
) -> ChatConversation:
    conversation = service.get_owned_conversation(
        session, owner_id=owner_id, conversation_id=conversation_id
    )
    if conversation is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="conversation not found")
    return conversation


def _conversation_out(conversation: ChatConversation) -> ChatConversationOut:
    return ChatConversationOut.model_validate(conversation)


def _message_out(message: ChatMessage) -> ChatMessageOut:
    fields = {
        field: getattr(message, field)
        for field in ChatMessageOut.model_fields
        if field not in {"category", "content_category"}
    }
    return ChatMessageOut.model_validate(
        {
            **fields,
            "content_category": (
                "owner_authored" if message.role is ChatRole.USER else "ai_generated"
            ),
        }
    )
