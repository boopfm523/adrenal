"""Authenticated API contract for persistent HealthCurve Chat working state."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from healthcurve.api.schemas import ApiModel, PageMetadata
from healthcurve.chat.models import ChatMessageState, ChatRole


class ChatConversationCreate(ApiModel):
    title: str = Field(default="New conversation", min_length=1, max_length=120)
    include_sensitive_text: bool = False
    model_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def title_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("title must not be blank")
        return value.strip()


class ChatConversationUpdate(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    include_sensitive_text: bool | None = None
    #: Send null to return to the default model; omit to leave the choice unchanged.
    model_name: str | None = Field(default=None, min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def optional_title_not_blank(cls, value: str | None) -> str | None:
        if value is not None and not value.strip():
            raise ValueError("title must not be blank")
        return None if value is None else value.strip()

    @model_validator(mode="after")
    def has_change(self) -> ChatConversationUpdate:
        if (
            self.title is None
            and self.include_sensitive_text is None
            and "model_name" not in self.model_fields_set
        ):
            raise ValueError("at least one conversation field is required")
        return self


class ChatModelOut(ApiModel):
    name: str
    parameter_size: str | None
    thinking: bool
    vision: bool
    default: bool


class ChatModelList(ApiModel):
    default_model: str
    ollama_reachable: bool
    models: list[ChatModelOut]


class ChatConversationOut(ApiModel):
    category: Literal["ai"] = "ai"
    id: uuid.UUID
    title: str
    include_sensitive_text: bool
    model_name: str | None
    created_at: datetime
    updated_at: datetime
    last_message_at: datetime | None
    retention_expires_at: datetime | None


class ChatConversationPage(ApiModel):
    items: list[ChatConversationOut]
    page: PageMetadata


class ChatUserMessageCreate(ApiModel):
    body: str = Field(min_length=1, max_length=8000)
    client_message_id: str = Field(min_length=1, max_length=128)

    @field_validator("body", "client_message_id")
    @classmethod
    def message_fields_not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must not be blank")
        return value.strip()


class ChatMessageOut(ApiModel):
    category: Literal["ai"] = "ai"
    content_category: Literal["owner_authored", "ai_generated"]
    id: uuid.UUID
    conversation_id: uuid.UUID
    role: ChatRole
    state: ChatMessageState
    body: str | None
    sequence: int
    generated_at: datetime | None
    model_name: str | None
    model_digest: str | None
    prompt_version: str | None
    schema_version: str | None
    tool_versions: dict[str, str] | None
    source_manifest: list[dict[str, object]] | None
    source_scope: dict[str, object] | None
    source_fingerprint: str | None
    error_code: str | None
    created_at: datetime
    updated_at: datetime


class ChatMessagePage(ApiModel):
    items: list[ChatMessageOut]
    page: PageMetadata


class ChatMessageStalenessOut(ApiModel):
    status: Literal["fresh", "stale", "unavailable", "not_applicable"]
    stale: bool | None
    checked_at: datetime
