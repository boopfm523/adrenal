"""Private-export inclusion rules for sensitive chatbot working state."""

from __future__ import annotations

import uuid

from healthcurve.private_exports.generator import (
    _collections,  # pyright: ignore[reportPrivateUsage]
)


def _ai_collection_names(*, include_ai: bool, include_sensitive: bool) -> set[str]:
    collections = _collections(
        uuid.uuid4(), include_ai=include_ai, include_sensitive=include_sensitive
    )
    return {collection.name for collection in collections["ai"]}


def test_chat_history_requires_both_ai_and_sensitive_export_options() -> None:
    chat_names = {"chat_conversations", "chat_messages", "chat_tool_executions"}

    assert chat_names.isdisjoint(_ai_collection_names(include_ai=False, include_sensitive=False))
    assert chat_names.isdisjoint(_ai_collection_names(include_ai=True, include_sensitive=False))
    assert chat_names.isdisjoint(_ai_collection_names(include_ai=False, include_sensitive=True))
    assert chat_names <= _ai_collection_names(include_ai=True, include_sensitive=True)
