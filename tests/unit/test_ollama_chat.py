"""Native Ollama tool calling for analytical chat (ADR-0036, hc-ixn3.5).

All data here is synthetic. Every failure must be a typed result, thinking text must
never escape the adapter, and a tool call is trusted only as far as its name being one
the caller offered.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
from structlog.testing import capture_logs

from healthcurve.ai.ollama import (
    BREAKER_THRESHOLD,
    MAX_TOOL_CALLS_PER_RESPONSE,
    ChatResult,
    ChatToolCall,
    ModelOutcome,
    OllamaClient,
    tool_result_message,
)
from healthcurve.config import Settings

Handler = Callable[[httpx.Request], httpx.Response]

THINKING_SENTINEL = "SYNTHETIC-THINKING-7f3a average bedtime reasoning"

TOOLS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "run_query",
            "description": "Run a read-only query over synthetic analytics views.",
            "parameters": {
                "type": "object",
                "properties": {"sql": {"type": "string"}},
                "required": ["sql"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "describe_data",
            "description": "Describe available views.",
            "parameters": {"type": "object", "properties": {}},
        },
    },
]

MESSAGES: list[dict[str, Any]] = [
    {"role": "system", "content": "SYSTEM RULES"},
    {"role": "user", "content": "What was my synthetic average bedtime?"},
]


def _client(**overrides: Any) -> OllamaClient:
    base: dict[str, Any] = {"_env_file": None, "ollama_base_url": "http://ollama:11434"}
    base.update(overrides)
    return OllamaClient(Settings(**base))


def _patch_transport(monkeypatch: pytest.MonkeyPatch, handler: Handler) -> None:
    def fake_init(self: httpx.Client, **kwargs: Any) -> None:
        original(self, transport=httpx.MockTransport(handler), **kwargs)

    original = httpx.Client.__init__
    monkeypatch.setattr(httpx.Client, "__init__", fake_init)


def _body(message: dict[str, Any], **top: Any) -> dict[str, Any]:
    body: dict[str, Any] = {
        "done": True,
        "done_reason": "stop",
        "eval_count": 42,
        "prompt_eval_count": 310,
        "total_duration": 1,
        "message": {"role": "assistant", **message},
    }
    body.update(top)
    return body


def _recording(
    monkeypatch: pytest.MonkeyPatch, message: dict[str, Any], **top: Any
) -> list[dict[str, Any]]:
    seen: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(json.loads(request.content))
        return httpx.Response(200, json=_body(message, **top))

    _patch_transport(monkeypatch, handler)
    return seen


def _chat(client: OllamaClient, **kwargs: Any) -> ChatResult:
    params: dict[str, Any] = {"messages": MESSAGES, "tools": TOOLS}
    params.update(kwargs)
    return client.chat(**params)


# ---------------------------------------------------------------------------
# Request shape
# ---------------------------------------------------------------------------


def test_request_payload_carries_tools_thinking_and_chat_limits(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _recording(monkeypatch, {"content": "Synthetic answer."})
    _chat(_client())

    payload = seen[0]
    assert payload["model"] == "qwen3:30b"
    assert payload["stream"] is False
    assert payload["think"] is True, "HC_CHAT_THINKING defaults to true"
    assert payload["tools"] == TOOLS
    assert payload["messages"] == MESSAGES
    assert payload["options"] == {"temperature": 0.0, "num_ctx": 24_576, "num_predict": 4096}
    assert payload["keep_alive"] == 300
    assert "format" not in payload


def test_chat_context_window_setting_overrides_the_ollama_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _recording(monkeypatch, {"content": "ok"})
    _chat(_client(chat_context_window=32_768, chat_max_output_tokens=1024))
    assert seen[0]["options"]["num_ctx"] == 32_768
    assert seen[0]["options"]["num_predict"] == 1024


def test_explicit_arguments_override_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _recording(monkeypatch, {"content": "ok"})
    _chat(
        _client(chat_context_window=32_768),
        think=False,
        temperature=0.2,
        context_window=8192,
        max_output_tokens=512,
    )
    assert seen[0]["think"] is False
    assert seen[0]["options"] == {"temperature": 0.2, "num_ctx": 8192, "num_predict": 512}


def test_thinking_setting_is_the_default(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = _recording(monkeypatch, {"content": "ok"})
    _chat(_client(chat_thinking=False))
    assert seen[0]["think"] is False


def test_keep_alive_is_sent_only_for_the_configured_text_model(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _recording(monkeypatch, {"content": "ok"})
    client = _client(ollama_keep_alive_s=-1)
    _chat(client)
    _chat(client, model_name="qwen3.8:27b")
    assert seen[0]["keep_alive"] == -1
    assert seen[1]["model"] == "qwen3.8:27b"
    assert "keep_alive" not in seen[1]


def test_chat_uses_the_chat_read_timeout(monkeypatch: pytest.MonkeyPatch) -> None:
    reads: list[float | None] = []
    original = httpx.Client.__init__

    def fake_init(self: httpx.Client, **kwargs: Any) -> None:
        reads.append(kwargs["timeout"].read)

        def handler(_request: httpx.Request) -> httpx.Response:
            return httpx.Response(200, json=_body({"content": "ok"}))

        original(self, transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx.Client, "__init__", fake_init)
    client = _client(chat_read_timeout_s=900)
    _chat(client)
    _chat(client, read_timeout_s=45)
    assert reads == [900, 45]


def test_assistant_and_tool_turns_are_replayed_without_thinking(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _recording(monkeypatch, {"content": "Synthetic final answer."})
    call = ChatToolCall(id="call_1", name="run_query", arguments={"sql": "SELECT 1"})
    prior = ChatResult(outcome=ModelOutcome.OK, tool_calls=(call,), thinking_chars=99)
    assistant = {**prior.assistant_message(), "thinking": THINKING_SENTINEL}
    messages = [*MESSAGES, assistant, tool_result_message(call, '{"rows": 1}')]

    _chat(_client(), messages=messages)

    sent = seen[0]["messages"]
    assert sent[2] == {
        "role": "assistant",
        "content": "",
        "tool_calls": [
            {"id": "call_1", "function": {"name": "run_query", "arguments": {"sql": "SELECT 1"}}}
        ],
    }
    assert sent[3] == {
        "role": "tool",
        "tool_name": "run_query",
        "content": '{"rows": 1}',
        "tool_call_id": "call_1",
    }
    assert THINKING_SENTINEL not in json.dumps(seen[0])


def test_tool_result_without_an_id_omits_tool_call_id() -> None:
    message = tool_result_message(ChatToolCall(id=None, name="describe_data", arguments={}), "x")
    assert message == {"role": "tool", "tool_name": "describe_data", "content": "x"}


@pytest.mark.parametrize(
    "kwargs",
    [
        {"messages": []},
        {"messages": [{"role": "developer", "content": "x"}]},
        {"tools": [{"type": "function", "function": {"description": "no name"}}]},
        {"tools": [{"type": "retrieval"}]},
        {"context_window": 0},
        {"max_output_tokens": 0},
        {"read_timeout_s": 0},
    ],
)
def test_caller_programming_errors_raise_before_any_request(
    monkeypatch: pytest.MonkeyPatch, kwargs: dict[str, Any]
) -> None:
    seen = _recording(monkeypatch, {"content": "ok"})
    with pytest.raises(ValueError):
        _chat(_client(), **kwargs)
    assert seen == []


# ---------------------------------------------------------------------------
# Response parsing
# ---------------------------------------------------------------------------


def test_tool_calls_with_object_and_string_arguments_are_parsed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _recording(
        monkeypatch,
        {
            "content": "",
            "thinking": THINKING_SENTINEL,
            "tool_calls": [
                {
                    "id": "call_a",
                    "function": {
                        "index": 0,
                        "name": "run_query",
                        "arguments": {"sql": "SELECT 17 + 25"},
                    },
                },
                {"function": {"index": 1, "name": "describe_data", "arguments": "{}"}},
            ],
        },
        done_reason="stop",
    )
    result = _chat(_client())

    assert result.ok
    assert result.content is None
    assert result.tool_calls == (
        ChatToolCall(id="call_a", name="run_query", arguments={"sql": "SELECT 17 + 25"}),
        ChatToolCall(id=None, name="describe_data", arguments={}),
    )
    assert result.done_reason == "stop"
    assert result.prompt_tokens == 310
    assert result.output_tokens == 42
    assert result.model_name == "qwen3:30b"
    assert result.latency_ms is not None
    assert result.thinking_chars == len(THINKING_SENTINEL)


def test_json_string_arguments_are_decoded(monkeypatch: pytest.MonkeyPatch) -> None:
    _recording(
        monkeypatch,
        {"tool_calls": [{"function": {"name": "run_query", "arguments": '{"sql": "SELECT 2"}'}}]},
    )
    result = _chat(_client())
    assert result.ok
    assert result.tool_calls[0].arguments == {"sql": "SELECT 2"}


def test_content_only_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    _recording(monkeypatch, {"content": "Synthetic mean bedtime 23:10 over 30 days."})
    result = _chat(_client())
    assert result.ok
    assert result.content == "Synthetic mean bedtime 23:10 over 30 days."
    assert result.tool_calls == ()
    assert result.assistant_message() == {
        "role": "assistant",
        "content": "Synthetic mean bedtime 23:10 over 30 days.",
    }


@pytest.mark.parametrize(
    ("tool_calls", "detail"),
    [
        ([{"function": {"name": "drop_tables", "arguments": {}}}], "tool_call_unknown_tool"),
        ([{"function": {"arguments": {}}}], "tool_call_missing_name"),
        ([{"function": {"name": "", "arguments": {}}}], "tool_call_missing_name"),
        ([{"function": "run_query"}], "tool_call_malformed"),
        (["run_query"], "tool_call_malformed"),
        ([{"function": {"name": "run_query", "arguments": "[1, 2]"}}], "tool_arguments_not_object"),
        ([{"function": {"name": "run_query", "arguments": [1]}}], "tool_arguments_not_object"),
        ([{"function": {"name": "run_query", "arguments": "not json"}}], "tool_arguments_not_json"),
        ({"function": {"name": "run_query"}}, "tool_calls_not_list"),
        (
            [{"function": {"name": "describe_data", "arguments": {}}}]
            * (MAX_TOOL_CALLS_PER_RESPONSE + 1),
            "too_many_tool_calls",
        ),
    ],
)
def test_malformed_tool_calls_are_invalid_json(
    monkeypatch: pytest.MonkeyPatch, tool_calls: object, detail: str
) -> None:
    _recording(monkeypatch, {"content": "partial", "tool_calls": tool_calls})
    result = _chat(_client())
    assert result.outcome is ModelOutcome.INVALID_JSON
    assert result.detail == detail
    assert result.content is None
    assert result.tool_calls == ()
    assert "drop_tables" not in repr(result)


def test_call_to_a_tool_when_none_were_offered_is_rejected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen = _recording(
        monkeypatch, {"tool_calls": [{"function": {"name": "run_query", "arguments": {}}}]}
    )
    result = _chat(_client(), tools=[])
    assert result.outcome is ModelOutcome.INVALID_JSON
    assert result.detail == "tool_call_unknown_tool"
    assert "tools" not in seen[0]


def test_maximum_tool_calls_is_accepted(monkeypatch: pytest.MonkeyPatch) -> None:
    _recording(
        monkeypatch,
        {
            "tool_calls": [{"function": {"name": "describe_data", "arguments": {}}}]
            * MAX_TOOL_CALLS_PER_RESPONSE
        },
    )
    result = _chat(_client())
    assert result.ok
    assert len(result.tool_calls) == MAX_TOOL_CALLS_PER_RESPONSE


@pytest.mark.parametrize(
    ("message", "detail"),
    [
        ({"content": ""}, "empty_response"),
        ({"content": "   \n"}, "empty_response"),
        ({"content": "", "thinking": THINKING_SENTINEL}, "empty_response"),
        ({"tool_calls": []}, "empty_response"),
        ({"content": 7}, "content_not_string"),
    ],
)
def test_empty_or_malformed_message_is_invalid_json(
    monkeypatch: pytest.MonkeyPatch, message: dict[str, Any], detail: str
) -> None:
    """Unlike generate_json, thinking is never promoted into the answer channel."""
    _recording(monkeypatch, message)
    result = _chat(_client())
    assert result.outcome is ModelOutcome.INVALID_JSON
    assert result.detail == detail
    assert result.content is None


@pytest.mark.parametrize(
    ("response", "detail"),
    [
        (httpx.Response(200, json={"done": True}), "missing_message"),
        (httpx.Response(200, json=[1, 2]), "response_not_object"),
        (httpx.Response(200, text="not json"), "response_not_json"),
        (
            httpx.Response(200, json={"done": False, "message": {"content": "part"}}),
            "incomplete_response",
        ),
    ],
)
def test_malformed_response_bodies_are_invalid_json(
    monkeypatch: pytest.MonkeyPatch, response: httpx.Response, detail: str
) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return response

    _patch_transport(monkeypatch, handler)
    result = _chat(_client())
    assert result.outcome is ModelOutcome.INVALID_JSON
    assert result.detail == detail


# ---------------------------------------------------------------------------
# Transport failures and breaker
# ---------------------------------------------------------------------------


def test_timeout_returns_typed_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("too slow", request=request)

    _patch_transport(monkeypatch, handler)
    result = _chat(_client())
    assert result.outcome is ModelOutcome.TIMEOUT
    assert result.detail == "timeout"
    assert result.content is None


def test_unreachable_returns_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    _patch_transport(monkeypatch, handler)
    result = _chat(_client())
    assert result.outcome is ModelOutcome.UNAVAILABLE
    assert result.detail == "unreachable"


def test_http_error_returns_error(monkeypatch: pytest.MonkeyPatch) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    _patch_transport(monkeypatch, handler)
    result = _chat(_client())
    assert result.outcome is ModelOutcome.ERROR
    assert result.detail == "http_status_500"


def test_open_breaker_short_circuits_without_a_network_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        raise httpx.ConnectError("refused", request=request)

    _patch_transport(monkeypatch, handler)
    client = _client()
    for _ in range(BREAKER_THRESHOLD):
        _chat(client)
    attempts_before = calls["n"]

    result = _chat(client)
    assert result.outcome is ModelOutcome.UNAVAILABLE
    assert result.detail == "circuit_breaker_open"
    assert calls["n"] == attempts_before


def test_a_model_that_rejects_think_is_retried_once_without_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    attempts: list[dict[str, Any]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        attempts.append(body)
        if "think" in body:
            return httpx.Response(400, json={"error": "model does not support thinking"})
        return httpx.Response(200, json=_body({"content": "Synthetic answer."}))

    _patch_transport(monkeypatch, handler)
    with capture_logs() as logs:
        result = _chat(_client())

    assert result.ok
    assert len(attempts) == 2
    assert attempts[0]["think"] is True
    assert "think" not in attempts[1]
    assert attempts[1]["tools"] == TOOLS
    assert any(entry.get("reason_code") == "think_unsupported" for entry in logs)


def test_persistent_400_is_an_error_after_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(400, json={"error": "bad request"})

    _patch_transport(monkeypatch, handler)
    result = _chat(_client())
    assert result.outcome is ModelOutcome.ERROR
    assert result.detail == "http_status_400"
    assert attempts["n"] == 2


# ---------------------------------------------------------------------------
# Privacy (C9)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "message",
    [
        {
            "content": "",
            "thinking": THINKING_SENTINEL,
            "tool_calls": [
                {"id": "call_x", "function": {"name": "run_query", "arguments": {"sql": "S"}}}
            ],
        },
        {"content": "Synthetic answer.", "thinking": THINKING_SENTINEL},
        {"content": "", "thinking": THINKING_SENTINEL},
        {
            "thinking": THINKING_SENTINEL,
            "tool_calls": [{"function": {"name": "unknown", "arguments": {}}}],
        },
    ],
)
def test_thinking_messages_and_arguments_never_reach_results_or_logs(
    monkeypatch: pytest.MonkeyPatch, message: dict[str, Any]
) -> None:
    _recording(monkeypatch, message)
    secret_prompt = "SYNTHETIC-PROMPT-91c2 bedtime question"
    messages = [{"role": "user", "content": secret_prompt}]
    with capture_logs() as logs:
        result = _chat(_client(), messages=messages)

    assert THINKING_SENTINEL not in repr(result)
    assert THINKING_SENTINEL not in json.dumps(result.assistant_message())
    assert result.thinking_chars == len(THINKING_SENTINEL)
    rendered_logs = json.dumps(logs, default=str)
    assert THINKING_SENTINEL not in rendered_logs
    assert secret_prompt not in rendered_logs
    assert "Synthetic answer." not in rendered_logs
    assert '"S"' not in rendered_logs
    assert logs, "outcome must still be observable"
    allowed = {"event", "log_level", "outcome", "model_name", "latency_ms", "count", "reason_code"}
    for entry in logs:
        assert set(entry) <= allowed


def test_generate_json_callers_are_unaffected_by_chat_settings(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Chat settings must not leak into extraction requests."""
    seen = _recording(monkeypatch, {"content": "{}"})
    client = _client(chat_context_window=32_768, chat_thinking=True, chat_read_timeout_s=900)
    result = client.generate_json(
        system_prompt="s", user_content="u", json_schema={"type": "object"}
    )
    assert result.ok
    assert seen[0]["think"] is False
    assert seen[0]["options"]["num_ctx"] == 24_576
    assert "num_predict" not in seen[0]["options"]
    assert "tools" not in seen[0]
