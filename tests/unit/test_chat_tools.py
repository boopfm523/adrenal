"""Contract tests for the private chatbot's bounded read-only data tools."""

from __future__ import annotations

import uuid
from datetime import date
from typing import Any, cast
from unittest import mock

import pytest
from sqlalchemy.orm import Session

from healthcurve.chat import tools


def valid_range() -> dict[str, object]:
    return {
        "date_from": "2026-08-01",
        "date_to": "2026-08-16",
        "timezone": "America/New_York",
    }


def test_catalog_exposes_only_the_nine_approved_read_tools() -> None:
    definitions = tools.tool_definitions()

    assert {item["name"] for item in definitions} == {
        "get_data_availability",
        "get_daily_healthcurve",
        "search_timeline",
        "get_medication_context",
        "get_symptom_episode_context",
        "get_wearable_context",
        "get_lab_trends",
        "compare_periods",
        "get_report_snapshot_context",
    }
    for definition in definitions:
        schema = cast(dict[str, Any], definition["input_schema"])
        properties = cast(dict[str, Any], schema.get("properties", {}))
        assert "owner_id" not in properties
        assert "sql" not in properties
        assert "table" not in properties
        assert schema.get("additionalProperties") is False


@pytest.mark.parametrize(
    ("tool_name", "arguments"),
    [
        ("search_timeline", {**valid_range(), "owner_id": str(uuid.uuid4())}),
        ("search_timeline", {**valid_range(), "sql": "SELECT * FROM identity.owner"}),
        ("get_data_availability", {**valid_range(), "date_to": "2027-08-16"}),
        ("get_data_availability", {**valid_range(), "timezone": "not/a-zone"}),
        ("search_timeline", {**valid_range(), "limit": 201}),
        (
            "get_wearable_context",
            {
                **valid_range(),
                "include_intraday": True,
                "bucket_minutes": 15,
            },
        ),
    ],
)
def test_model_arguments_cannot_escape_bounded_contract(
    tool_name: str, arguments: dict[str, object]
) -> None:
    with pytest.raises(tools.ChatToolError, match="invalid_tool_arguments"):
        tools.validate_tool_arguments(tool_name, arguments)


def test_unknown_tool_is_rejected_before_database_access() -> None:
    session = mock.create_autospec(Session, instance=True)

    with pytest.raises(tools.ChatToolError, match="unknown_tool"):
        tools.execute_chat_tool(
            session,
            owner_id=uuid.uuid4(),
            tool_name="run_sql",
            arguments={"sql": "SELECT 1"},
        )

    session.assert_not_called()


def test_sensitive_text_requires_conversation_level_permission() -> None:
    session = mock.create_autospec(Session, instance=True)

    with pytest.raises(tools.ChatToolError, match="sensitive_text_not_enabled"):
        tools.execute_chat_tool(
            session,
            owner_id=uuid.uuid4(),
            tool_name="search_timeline",
            arguments={**valid_range(), "include_sensitive_text": True},
        )

    session.execute.assert_not_called()


def test_authenticated_owner_is_injected_separately_from_model_arguments(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid.uuid4()
    session = cast(Session, mock.Mock(spec=Session))
    seen: dict[str, object] = {}

    def fake_handler(
        received_session: Session,
        received_owner_id: uuid.UUID,
        arguments: tools.DataAvailabilityArguments,
    ) -> tools.ChatToolResult:
        seen.update(
            session=received_session,
            owner_id=received_owner_id,
            arguments=arguments,
        )
        return tools.ChatToolResult(
            tool_name="get_data_availability",
            data={"counts": {}},
            missingness={"missing_domains": []},
            source_manifest={"scope": []},
            result_sha256="0" * 64,
        )

    read_only = mock.Mock()
    monkeypatch.setattr(tools, "_read_only", read_only)
    handlers = cast(
        dict[str, Any],
        tools._HANDLERS,  # pyright: ignore[reportPrivateUsage]
    )
    monkeypatch.setitem(handlers, "get_data_availability", fake_handler)

    result = tools.execute_chat_tool(
        session,
        owner_id=owner_id,
        tool_name="get_data_availability",
        arguments=valid_range(),
    )

    assert result.tool_name == "get_data_availability"
    assert seen["session"] is session
    assert seen["owner_id"] == owner_id
    parsed = cast(tools.DataAvailabilityArguments, seen["arguments"])
    assert parsed.date_from == date(2026, 8, 1)
    assert not hasattr(parsed, "owner_id")
    read_only.assert_called_once_with(session)
