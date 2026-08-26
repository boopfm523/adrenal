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
from dataclasses import dataclass
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

    def _failed(
        self, outcome: ModelOutcome, started: float, detail: str, model_name: str
    ) -> ModelResult:
        latency_ms = int((time.monotonic() - started) * 1000)
        self._telemetry.record(OperationalEvent.MODEL_FAILURE)
        # Outcome and timing only -- never the prompt or any partial completion (C9).
        log.warning(
            "model call failed",
            outcome=outcome.value,
            model_name=model_name,
            latency_ms=latency_ms,
            reason_code=outcome.value,
        )
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
