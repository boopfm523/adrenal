"""The only path to the local model (ADR-0003).

Everything about this adapter is shaped by one assumption: **the model will be
unavailable, slow, or wrong, and none of that may harm the record.** So every failure
is a typed result the caller must handle, not an exception that might escape into a
write path.

The base URL is validated as private at startup (see :mod:`healthcurve.config`).
Prompts and completions are class C9 and never logged.
"""

from __future__ import annotations

import json
import time
from base64 import b64encode
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Final

import httpx

from healthcurve.config import Settings, get_settings
from healthcurve.logging import get_logger
from healthcurve.operations.telemetry import OperationalEvent, OperationalTelemetry

log = get_logger(__name__)

#: Consecutive failures before the breaker opens.
BREAKER_THRESHOLD: Final = 3
#: How long the breaker stays open before a single trial request is allowed.
BREAKER_RESET_SECONDS: Final = 60.0
#: Tool calls accepted in one chat response. More than this is treated as a malformed
#: response rather than silently truncated, so the loop never runs a partial plan.
MAX_TOOL_CALLS_PER_RESPONSE: Final = 8
#: Message roles the chat endpoint accepts.
CHAT_ROLES: Final = frozenset({"system", "user", "assistant", "tool"})


class ModelOutcome(StrEnum):
    OK = "ok"
    UNAVAILABLE = "unavailable"  # connection refused, DNS, breaker open
    TIMEOUT = "timeout"
    INVALID_JSON = "invalid_json"  # responded, but not to the schema
    ERROR = "error"  # anything else


@dataclass(frozen=True, slots=True)
class ModelResult:
    """The result of asking the model. ``data`` is only ever set when ``ok``."""

    outcome: ModelOutcome
    data: dict[str, Any] | None = None
    model_name: str | None = None
    model_digest: str | None = None
    latency_ms: int | None = None
    detail: str | None = None

    @property
    def ok(self) -> bool:
        return self.outcome is ModelOutcome.OK


@dataclass(frozen=True, slots=True)
class ModelIdentity:
    name: str
    digest: str


@dataclass(frozen=True, slots=True)
class ChatToolCall:
    """One validated tool call. ``arguments`` is untrusted model output (SAFE-19).

    The name is guaranteed to be one of the offered tools; the arguments are only
    guaranteed to be a JSON object and must still be validated by the tool.
    """

    id: str | None
    name: str
    arguments: dict[str, Any]

    def wire_format(self) -> dict[str, Any]:
        """The Ollama representation used when replaying an assistant turn."""
        call: dict[str, Any] = {"function": {"name": self.name, "arguments": self.arguments}}
        if self.id is not None:
            call["id"] = self.id
        return call


@dataclass(frozen=True, slots=True)
class ChatResult:
    """The result of one chat turn.

    ``content`` and ``tool_calls`` are only ever set when ``ok``. Thinking text is
    transient C9 data (ADR-0036): it is never carried here, only its length.
    """

    outcome: ModelOutcome
    content: str | None = None
    tool_calls: tuple[ChatToolCall, ...] = field(default_factory=tuple)
    done_reason: str | None = None
    prompt_tokens: int | None = None
    output_tokens: int | None = None
    model_name: str | None = None
    model_digest: str | None = None
    latency_ms: int | None = None
    detail: str | None = None
    thinking_chars: int = 0

    @property
    def ok(self) -> bool:
        return self.outcome is ModelOutcome.OK

    def assistant_message(self) -> dict[str, Any]:
        """Replay this turn as an assistant message, without any thinking text."""
        message: dict[str, Any] = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            message["tool_calls"] = [call.wire_format() for call in self.tool_calls]
        return message


def tool_result_message(call: ChatToolCall, content: str) -> dict[str, Any]:
    """Build the message that returns a tool's (already budgeted) result to the model."""
    message: dict[str, Any] = {"role": "tool", "tool_name": call.name, "content": content}
    if call.id is not None:
        message["tool_call_id"] = call.id
    return message


class _MalformedChatResponseError(Exception):
    """Internal: the response did not match the tool-calling contract.

    Carries only a fixed reason code, never any model-authored text.
    """

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _offered_tool_names(tools: Sequence[Mapping[str, Any]]) -> frozenset[str]:
    names: set[str] = set()
    for tool in tools:
        if tool.get("type") != "function":
            raise ValueError("each tool must be a function tool with a non-empty name")
        function = tool.get("function")
        name = function.get("name") if isinstance(function, Mapping) else None
        if not isinstance(name, str) or not name:
            raise ValueError("each tool must be a function tool with a non-empty name")
        names.add(name)
    return frozenset(names)


def _outgoing_messages(messages: Sequence[Mapping[str, Any]]) -> list[dict[str, Any]]:
    if not messages:
        raise ValueError("messages must not be empty")
    outgoing: list[dict[str, Any]] = []
    for message in messages:
        if message.get("role") not in CHAT_ROLES:
            raise ValueError("each message must have a supported role")
        # Thinking is never replayed: it is transient and must not be re-sent (C9).
        outgoing.append({k: v for k, v in message.items() if k != "thinking"})
    return outgoing


def _parse_tool_arguments(raw: object) -> dict[str, Any]:
    if raw is None:
        return {}
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError:
            raise _MalformedChatResponseError("tool_arguments_not_json") from None
    if not isinstance(raw, dict):
        raise _MalformedChatResponseError("tool_arguments_not_object")
    return {str(key): value for key, value in raw.items()}


def _parse_tool_calls(raw: object, offered: frozenset[str]) -> tuple[ChatToolCall, ...]:
    if raw is None:
        return ()
    if not isinstance(raw, list):
        raise _MalformedChatResponseError("tool_calls_not_list")
    if len(raw) > MAX_TOOL_CALLS_PER_RESPONSE:
        raise _MalformedChatResponseError("too_many_tool_calls")
    calls: list[ChatToolCall] = []
    for item in raw:
        function = item.get("function") if isinstance(item, dict) else None
        if not isinstance(function, dict):
            raise _MalformedChatResponseError("tool_call_malformed")
        name = function.get("name")
        if not isinstance(name, str) or not name:
            raise _MalformedChatResponseError("tool_call_missing_name")
        if name not in offered:
            raise _MalformedChatResponseError("tool_call_unknown_tool")
        call_id = item.get("id")
        calls.append(
            ChatToolCall(
                id=call_id if isinstance(call_id, str) and call_id else None,
                name=name,
                arguments=_parse_tool_arguments(function.get("arguments")),
            )
        )
    return tuple(calls)


def _optional_int(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _parse_model_json(content: str) -> object:
    """Parse a single model JSON object, tolerating presentation-only fences.

    Some local models wrap otherwise schema-constrained JSON in a Markdown fence or
    add a short preamble. The result remains untrusted and caller-owned schema
    validation is still mandatory. Only one complete object with whitespace or a
    closing fence after it is accepted; arbitrary trailing model text is rejected.
    """
    try:
        return json.loads(content)
    except json.JSONDecodeError as original_error:
        start = content.find("{")
        if start < 0:
            raise original_error
        try:
            value, end = json.JSONDecoder().raw_decode(content[start:])
        except json.JSONDecodeError:
            raise original_error from None
        trailing = content[start + end :].strip()
        if trailing not in ("", "```"):
            raise original_error
        return value


class CircuitBreaker:
    """Stops hammering a model that is down.

    Deliberately simple: a failed trial request re-opens the breaker, so a model that
    is down stays cheap to be down.
    """

    def __init__(self) -> None:
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def is_open(self) -> bool:
        if self._opened_at is None:
            return False
        if time.monotonic() - self._opened_at >= BREAKER_RESET_SECONDS:
            self._opened_at = None
            self._failures = 0
            return False
        return True

    def record_success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def record_failure(self) -> None:
        self._failures += 1
        if self._failures >= BREAKER_THRESHOLD:
            self._opened_at = time.monotonic()


@dataclass(frozen=True, slots=True)
class LocalChatModel:
    """A model installed in the local Ollama that can run tool-calling chat."""

    name: str
    digest: str
    parameter_size: str | None
    thinking: bool
    vision: bool


def _is_remote(entry: Mapping[str, Any]) -> bool:
    # Ollama cloud models forward prompts to a remote service; private data stays local.
    name = str(entry.get("name") or entry.get("model") or "")
    return bool(entry.get("remote_host") or entry.get("remote_model")) or name.endswith(
        ("-cloud", ":cloud")
    )


class OllamaClient:
    """Schema-constrained JSON generation against a private Ollama."""

    def __init__(self, settings: Settings | None = None) -> None:
        self._settings = settings or get_settings()
        self._breaker = CircuitBreaker()
        self._telemetry = OperationalTelemetry(self._settings.redis_url)

    @property
    def model_name(self) -> str:
        return self._settings.ollama_model

    def identity(self, model_name: str | None = None) -> ModelIdentity | None:
        """Resolve the configured tag to the immutable local Ollama digest."""
        selected_model = model_name or self._settings.ollama_model
        try:
            with httpx.Client(
                base_url=self._settings.ollama_base_url, timeout=httpx.Timeout(3.0)
            ) as client:
                response = client.get("/api/tags")
                response.raise_for_status()
                models = response.json().get("models", [])
        except (httpx.HTTPError, ValueError):
            return None
        for model in models:
            if model.get("name") == selected_model:
                digest = model.get("digest")
                if isinstance(digest, str) and digest:
                    return ModelIdentity(name=selected_model, digest=digest)
        return None

    def for_model(self, model_name: str) -> OllamaClient:
        """A client whose default model is ``model_name``; every other setting is shared."""
        return OllamaClient(self._settings.model_copy(update={"ollama_model": model_name}))

    def chat_models(self) -> tuple[LocalChatModel, ...] | None:
        """Installed local models that support tool calling; None when Ollama is unreachable.

        Remote (Ollama cloud) models are never listed: private health data must not leave
        this computer (ADR-0036). Only metadata is read, so no model is loaded.
        """
        models: list[LocalChatModel] = []
        try:
            with httpx.Client(
                base_url=self._settings.ollama_base_url, timeout=httpx.Timeout(5.0)
            ) as client:
                response = client.get("/api/tags")
                response.raise_for_status()
                for entry in response.json().get("models", []):
                    if not isinstance(entry, dict) or _is_remote(entry):
                        continue
                    name, digest = entry.get("name"), entry.get("digest")
                    if not isinstance(name, str) or not isinstance(digest, str) or not digest:
                        continue
                    shown = client.post("/api/show", json={"model": name})
                    if not shown.is_success:
                        continue
                    detail = shown.json()
                    if not isinstance(detail, dict) or _is_remote(detail):
                        continue
                    capabilities = detail.get("capabilities") or []
                    if "tools" not in capabilities or "completion" not in capabilities:
                        continue
                    details = entry.get("details")
                    size = details.get("parameter_size") if isinstance(details, dict) else None
                    models.append(
                        LocalChatModel(
                            name=name,
                            digest=digest,
                            parameter_size=size if isinstance(size, str) else None,
                            thinking="thinking" in capabilities,
                            vision="vision" in capabilities,
                        )
                    )
        except (httpx.HTTPError, ValueError, AttributeError):
            return None
        return tuple(sorted(models, key=lambda model: model.name))

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
        model_name: str | None = None,
        images: list[bytes] | None = None,
        max_output_tokens: int | None = None,
        context_window: int | None = None,
        read_timeout_s: float | None = None,
    ) -> ModelResult:
        """Ask for a JSON object matching ``json_schema``.

        ``user_content`` is untrusted (SAFE-19). It is placed in the user turn as data,
        never merged into the system prompt, and the caller must still validate the
        result against a Pydantic model before trusting any of it.
        """
        selected_model = model_name or self._settings.ollama_model
        if self._breaker.is_open:
            self._telemetry.record(OperationalEvent.MODEL_FAILURE)
            return ModelResult(
                outcome=ModelOutcome.UNAVAILABLE,
                model_name=selected_model,
                detail="circuit breaker open after repeated failures",
            )

        user_message: dict[str, Any] = {"role": "user", "content": user_content}
        if images:
            user_message["images"] = [b64encode(image).decode("ascii") for image in images]
        if max_output_tokens is not None and max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if context_window is not None and context_window <= 0:
            raise ValueError("context_window must be positive")
        if read_timeout_s is not None and read_timeout_s <= 0:
            raise ValueError("read_timeout_s must be positive")
        options: dict[str, Any] = {
            "temperature": temperature,
            "num_ctx": (
                self._settings.ollama_context_window if context_window is None else context_window
            ),
        }
        if max_output_tokens is not None:
            options["num_predict"] = max_output_tokens
        payload: dict[str, Any] = {
            "model": selected_model,
            "stream": False,
            # Ollama's structured-output mode. Constrains decoding to the schema, which
            # makes invalid JSON rare -- but never assumed away.
            "format": json_schema,
            "options": options,
            "messages": [
                {"role": "system", "content": system_prompt},
                user_message,
            ],
        }
        if selected_model == self._settings.ollama_model and not images:
            # This controls host-native Ollama residency only. It does not start a
            # model container or alter the separate vision model lifecycle.
            payload["keep_alive"] = self._settings.ollama_keep_alive_s
        if not self._settings.ollama_thinking:
            # Measured on qwen3:30b-a3b: 29.1s with reasoning, 1.9s without, for the
            # same extraction. Accuracy did not drop -- amounts, ISO times, negation
            # and hypotheticals were all read correctly, and more consistently. This is
            # a parsing task with a constrained output schema; there is nothing for a
            # reasoning phase to work out.
            payload["think"] = False

        started = time.monotonic()
        try:
            with httpx.Client(
                base_url=self._settings.ollama_base_url,
                timeout=httpx.Timeout(
                    connect=self._settings.ollama_connect_timeout_s,
                    read=(
                        self._settings.ollama_read_timeout_s
                        if read_timeout_s is None
                        else read_timeout_s
                    ),
                    write=self._settings.ollama_connect_timeout_s,
                    pool=self._settings.ollama_connect_timeout_s,
                ),
            ) as client:
                response = client.post("/api/chat", json=payload)
                if response.status_code == 400 and "think" in payload:
                    # Older Ollama builds and non-reasoning models reject the field.
                    # Losing the speed-up beats losing the call.
                    log.info(
                        "model does not accept the think field; retrying without it",
                        model_name=selected_model,
                        reason_code="think_unsupported",
                    )
                    payload.pop("think")
                    response = client.post("/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
                if body.get("done") is False and "think" in payload:
                    # Some Ollama/qwen combinations accept ``think: false`` but
                    # return only an initial, truncated chunk despite stream=false.
                    # Retry once without the optional control rather than treating a
                    # transport-level partial object as model-authored invalid JSON.
                    log.info(
                        "model returned an incomplete non-streaming response; "
                        "retrying without the think field",
                        model_name=selected_model,
                        reason_code="think_incomplete_response",
                    )
                    payload.pop("think")
                    response = client.post("/api/chat", json=payload)
                    response.raise_for_status()
                    body = response.json()
        except httpx.TimeoutException:
            self._breaker.record_failure()
            return self._failed(ModelOutcome.TIMEOUT, started, "model timed out", selected_model)
        except httpx.HTTPStatusError as exc:
            self._breaker.record_failure()
            return self._failed(
                ModelOutcome.ERROR,
                started,
                f"model returned HTTP {exc.response.status_code}",
                selected_model,
            )
        except httpx.HTTPError:
            self._breaker.record_failure()
            return self._failed(
                ModelOutcome.UNAVAILABLE, started, "model unreachable", selected_model
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        message = body.get("message") or {}
        content = message.get("content")
        if not content and isinstance(message.get("thinking"), str):
            # Ollama 0.32 + qwen3-vl places grammar-constrained JSON in
            # ``thinking`` when ``think: false`` and leaves ``content`` empty.
            # Treat it as another untrusted model channel; the same JSON and
            # caller-owned Pydantic validation still apply before use.
            content = message["thinking"]
        if not content:
            self._breaker.record_failure()
            return self._failed(
                ModelOutcome.INVALID_JSON, started, "empty response", selected_model
            )

        try:
            data = _parse_model_json(content)
        except json.JSONDecodeError:
            self._breaker.record_failure()
            return self._failed(
                ModelOutcome.INVALID_JSON, started, "response was not JSON", selected_model
            )

        if not isinstance(data, dict):
            self._breaker.record_failure()
            return self._failed(
                ModelOutcome.INVALID_JSON, started, "response was not an object", selected_model
            )

        self._breaker.record_success()
        log.info(
            "model call",
            outcome=ModelOutcome.OK.value,
            model_name=selected_model,
            latency_ms=latency_ms,
            schema_valid=True,
        )
        return ModelResult(
            outcome=ModelOutcome.OK,
            data=data,
            model_name=selected_model,
            model_digest=body.get("model_digest"),
            latency_ms=latency_ms,
        )

    def chat(
        self,
        *,
        messages: Sequence[Mapping[str, Any]],
        tools: Sequence[Mapping[str, Any]],
        think: bool | None = None,
        temperature: float = 0.0,
        context_window: int | None = None,
        max_output_tokens: int | None = None,
        read_timeout_s: float | None = None,
        model_name: str | None = None,
    ) -> ChatResult:
        """Run one turn of a native Ollama tool-calling conversation (ADR-0036).

        ``messages`` carries prior turns, including assistant tool calls
        (:meth:`ChatResult.assistant_message`) and tool results
        (:func:`tool_result_message`). Everything in them other than the system
        prompt is untrusted data (SAFE-19). ``tools`` uses Ollama's function-tool
        format; a returned call to any other name is rejected.

        ``think`` defaults to ``HC_CHAT_THINKING``. Thinking text is never returned,
        logged, or stored; only its character count is exposed for telemetry.
        """
        selected_model = model_name or self._settings.ollama_model
        offered = _offered_tool_names(tools)
        outgoing = _outgoing_messages(messages)
        if max_output_tokens is not None and max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if context_window is not None and context_window <= 0:
            raise ValueError("context_window must be positive")
        if read_timeout_s is not None and read_timeout_s <= 0:
            raise ValueError("read_timeout_s must be positive")

        if self._breaker.is_open:
            self._telemetry.record(OperationalEvent.MODEL_FAILURE)
            return ChatResult(
                outcome=ModelOutcome.UNAVAILABLE,
                model_name=selected_model,
                detail="circuit_breaker_open",
            )

        num_ctx = context_window or (
            self._settings.chat_context_window or self._settings.ollama_context_window
        )
        payload: dict[str, Any] = {
            "model": selected_model,
            "stream": False,
            "think": self._settings.chat_thinking if think is None else think,
            "options": {
                "temperature": temperature,
                "num_ctx": num_ctx,
                "num_predict": max_output_tokens or self._settings.chat_max_output_tokens,
            },
            "messages": outgoing,
        }
        if tools:
            payload["tools"] = [dict(tool) for tool in tools]
        if selected_model == self._settings.ollama_model:
            # Host-native text-model residency only, as for generate_json.
            payload["keep_alive"] = self._settings.ollama_keep_alive_s

        started = time.monotonic()
        try:
            with httpx.Client(
                base_url=self._settings.ollama_base_url,
                timeout=httpx.Timeout(
                    connect=self._settings.ollama_connect_timeout_s,
                    read=(
                        self._settings.chat_read_timeout_s
                        if read_timeout_s is None
                        else read_timeout_s
                    ),
                    write=self._settings.ollama_connect_timeout_s,
                    pool=self._settings.ollama_connect_timeout_s,
                ),
            ) as client:
                response = client.post("/api/chat", json=payload)
                if response.status_code == 400 and "think" in payload:
                    # Non-reasoning models reject the field. Answering without the
                    # reasoning phase beats not answering.
                    log.info(
                        "model does not accept the think field; retrying without it",
                        model_name=selected_model,
                        reason_code="think_unsupported",
                    )
                    payload.pop("think")
                    response = client.post("/api/chat", json=payload)
                response.raise_for_status()
                body: object = response.json()
        except httpx.TimeoutException:
            return self._chat_failed(ModelOutcome.TIMEOUT, started, "timeout", selected_model)
        except httpx.HTTPStatusError as exc:
            return self._chat_failed(
                ModelOutcome.ERROR,
                started,
                f"http_status_{exc.response.status_code}",
                selected_model,
            )
        except httpx.HTTPError:
            return self._chat_failed(
                ModelOutcome.UNAVAILABLE, started, "unreachable", selected_model
            )
        except ValueError:
            return self._chat_failed(
                ModelOutcome.INVALID_JSON, started, "response_not_json", selected_model
            )

        thinking_chars = 0
        try:
            if not isinstance(body, dict):
                raise _MalformedChatResponseError("response_not_object")
            if body.get("done") is False:
                raise _MalformedChatResponseError("incomplete_response")
            message = body.get("message")
            if not isinstance(message, dict):
                raise _MalformedChatResponseError("missing_message")
            thinking = message.get("thinking")
            thinking_chars = len(thinking) if isinstance(thinking, str) else 0
            raw_content = message.get("content")
            if raw_content is not None and not isinstance(raw_content, str):
                raise _MalformedChatResponseError("content_not_string")
            tool_calls = _parse_tool_calls(message.get("tool_calls"), offered)
            content = raw_content if raw_content and raw_content.strip() else None
            if content is None and not tool_calls:
                raise _MalformedChatResponseError("empty_response")
        except _MalformedChatResponseError as exc:
            return self._chat_failed(
                ModelOutcome.INVALID_JSON,
                started,
                exc.code,
                selected_model,
                thinking_chars=thinking_chars,
            )

        latency_ms = int((time.monotonic() - started) * 1000)
        self._breaker.record_success()
        # Counts and timing only -- never messages, arguments, or completions (C9).
        log.info(
            "model chat call",
            outcome=ModelOutcome.OK.value,
            model_name=selected_model,
            latency_ms=latency_ms,
            count=len(tool_calls),
        )
        done_reason = body.get("done_reason")
        model_digest = body.get("model_digest")
        return ChatResult(
            outcome=ModelOutcome.OK,
            content=content,
            tool_calls=tool_calls,
            done_reason=done_reason if isinstance(done_reason, str) else None,
            prompt_tokens=_optional_int(body.get("prompt_eval_count")),
            output_tokens=_optional_int(body.get("eval_count")),
            model_name=selected_model,
            model_digest=model_digest if isinstance(model_digest, str) else None,
            latency_ms=latency_ms,
            thinking_chars=thinking_chars,
        )

    def _chat_failed(
        self,
        outcome: ModelOutcome,
        started: float,
        detail: str,
        model_name: str,
        *,
        thinking_chars: int = 0,
    ) -> ChatResult:
        """``detail`` is a fixed reason code; it never contains model or owner text."""
        self._breaker.record_failure()
        latency_ms = self._record_failure(outcome, started, model_name, reason_code=detail)
        return ChatResult(
            outcome=outcome,
            model_name=model_name,
            latency_ms=latency_ms,
            detail=detail,
            thinking_chars=thinking_chars,
        )

    def _record_failure(
        self, outcome: ModelOutcome, started: float, model_name: str, *, reason_code: str
    ) -> int:
        latency_ms = int((time.monotonic() - started) * 1000)
        self._telemetry.record(OperationalEvent.MODEL_FAILURE)
        # Outcome and timing only -- never the prompt or any partial completion (C9).
        log.warning(
            "model call failed",
            outcome=outcome.value,
            model_name=model_name,
            latency_ms=latency_ms,
            reason_code=reason_code,
        )
        return latency_ms

    def _failed(
        self, outcome: ModelOutcome, started: float, detail: str, model_name: str
    ) -> ModelResult:
        latency_ms = self._record_failure(outcome, started, model_name, reason_code=outcome.value)
        return ModelResult(
            outcome=outcome,
            model_name=model_name,
            latency_ms=latency_ms,
            detail=detail,
        )

    def health(self) -> bool:
        """True if the model service answers. Used by the integrations status page."""
        try:
            with httpx.Client(
                base_url=self._settings.ollama_base_url, timeout=httpx.Timeout(3.0)
            ) as client:
                return client.get("/api/tags").status_code == 200
        except httpx.HTTPError:
            return False
