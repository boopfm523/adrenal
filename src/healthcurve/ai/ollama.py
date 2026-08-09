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
from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Final

import httpx

from healthcurve.config import Settings, get_settings
from healthcurve.logging import get_logger

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

    @property
    def model_name(self) -> str:
        return self._settings.ollama_model

    def generate_json(
        self,
        *,
        system_prompt: str,
        user_content: str,
        json_schema: dict[str, Any],
        temperature: float = 0.0,
    ) -> ModelResult:
        """Ask for a JSON object matching ``json_schema``.

        ``user_content`` is untrusted (SAFE-19). It is placed in the user turn as data,
        never merged into the system prompt, and the caller must still validate the
        result against a Pydantic model before trusting any of it.
        """
        if self._breaker.is_open:
            return ModelResult(
                outcome=ModelOutcome.UNAVAILABLE,
                detail="circuit breaker open after repeated failures",
            )

        payload = {
            "model": self._settings.ollama_model,
            "stream": False,
            # Ollama's structured-output mode. Constrains decoding to the schema, which
            # makes invalid JSON rare -- but never assumed away.
            "format": json_schema,
            "options": {"temperature": temperature},
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_content},
            ],
        }

        started = time.monotonic()
        try:
            with httpx.Client(
                base_url=self._settings.ollama_base_url,
                timeout=httpx.Timeout(
                    connect=self._settings.ollama_connect_timeout_s,
                    read=self._settings.ollama_read_timeout_s,
                    write=self._settings.ollama_connect_timeout_s,
                    pool=self._settings.ollama_connect_timeout_s,
                ),
            ) as client:
                response = client.post("/api/chat", json=payload)
                response.raise_for_status()
                body = response.json()
        except httpx.TimeoutException:
            self._breaker.record_failure()
            return self._failed(ModelOutcome.TIMEOUT, started, "model timed out")
        except httpx.HTTPStatusError as exc:
            self._breaker.record_failure()
            return self._failed(
                ModelOutcome.ERROR, started, f"model returned HTTP {exc.response.status_code}"
            )
        except httpx.HTTPError:
            self._breaker.record_failure()
            return self._failed(ModelOutcome.UNAVAILABLE, started, "model unreachable")

        latency_ms = int((time.monotonic() - started) * 1000)
        content = (body.get("message") or {}).get("content")
        if not content:
            self._breaker.record_failure()
            return self._failed(ModelOutcome.INVALID_JSON, started, "empty response")

        try:
            data = json.loads(content)
        except json.JSONDecodeError:
            self._breaker.record_failure()
            return self._failed(ModelOutcome.INVALID_JSON, started, "response was not JSON")

        if not isinstance(data, dict):
            self._breaker.record_failure()
            return self._failed(ModelOutcome.INVALID_JSON, started, "response was not an object")

        self._breaker.record_success()
        log.info(
            "model call",
            outcome=ModelOutcome.OK.value,
            model_name=self._settings.ollama_model,
            latency_ms=latency_ms,
            schema_valid=True,
        )
        return ModelResult(
            outcome=ModelOutcome.OK,
            data=data,
            model_name=self._settings.ollama_model,
            model_digest=body.get("model_digest"),
            latency_ms=latency_ms,
        )

    def _failed(self, outcome: ModelOutcome, started: float, detail: str) -> ModelResult:
        latency_ms = int((time.monotonic() - started) * 1000)
        # Outcome and timing only -- never the prompt or any partial completion (C9).
        log.warning(
            "model call failed",
            outcome=outcome.value,
            model_name=self._settings.ollama_model,
            latency_ms=latency_ms,
            reason_code=outcome.value,
        )
        return ModelResult(
            outcome=outcome,
            model_name=self._settings.ollama_model,
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
