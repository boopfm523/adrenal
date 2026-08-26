"""Deterministic qualification checks for a non-default local Ollama model."""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field

from healthcurve.ai.ollama import OllamaClient
from healthcurve.config import Settings

QWEN38_CANDIDATE_MODEL: Final = "qwen3.8:27b-q8_0"
# The pulled Q8 artifact declares this minimum in ``ollama show``. Ollama 0.33.0
# remains the recommended host update, but qualification should test the model's
# actual compatibility boundary instead of rejecting a supported runtime.
QWEN38_MIN_OLLAMA_VERSION: Final = "0.32.12"
QWEN38_CONTEXT_WINDOW: Final = 24_576
QWEN38_MAX_OUTPUT_TOKENS: Final = 32

_VERSION = re.compile(r"^(\d+)\.(\d+)\.(\d+)")
_PROBE_SCHEMA: Final[dict[str, object]] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["status"],
    "properties": {"status": {"type": "string", "const": "ready"}},
}


class CandidatePreflightError(RuntimeError):
    """A safe, reason-coded candidate qualification failure."""


class CandidatePreflightReport(BaseModel):
    """Non-sensitive evidence that a candidate is callable through HealthCurve."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    base_url: str
    ollama_version: str
    minimum_ollama_version: str
    model_name: str
    model_digest: str = Field(min_length=32)
    thinking_enabled: bool
    context_window: int
    structured_output: bool
    latency_ms: int = Field(ge=0)


class CandidateSuiteResult(BaseModel):
    """One all-synthetic qualification suite result."""

    model_config = ConfigDict(extra="forbid")

    name: str
    passed: bool
    duration_ms: int = Field(ge=0)
    report_path: str
    failure_detail: str | None = None


class CandidateQualificationReport(BaseModel):
    """Aggregate evidence used by the later owner cutover decision."""

    model_config = ConfigDict(extra="forbid")

    generated_at: datetime
    candidate: CandidatePreflightReport
    suites: list[CandidateSuiteResult]
    passed: bool


def parse_ollama_version(value: str) -> tuple[int, int, int]:
    """Return the comparable numeric prefix from an Ollama version string."""
    match = _VERSION.match(value.strip().removeprefix("v"))
    if match is None:
        raise CandidatePreflightError("ollama_version_invalid")
    major, minor, patch = (int(part) for part in match.groups())
    return major, minor, patch


def run_candidate_preflight(
    settings: Settings,
    *,
    model_name: str = QWEN38_CANDIDATE_MODEL,
    minimum_ollama_version: str = QWEN38_MIN_OLLAMA_VERSION,
) -> CandidatePreflightReport:
    """Verify version, immutable identity, and the exact structured-output path."""
    candidate_settings = settings.model_copy(
        update={"ollama_model": model_name, "ollama_thinking": False}
    )
    try:
        with httpx.Client(
            base_url=candidate_settings.ollama_base_url,
            timeout=httpx.Timeout(candidate_settings.ollama_connect_timeout_s),
        ) as client:
            version_response = client.get("/api/version")
            version_response.raise_for_status()
            version = version_response.json().get("version")
            if not isinstance(version, str):
                raise CandidatePreflightError("ollama_version_missing")
            if parse_ollama_version(version) < parse_ollama_version(minimum_ollama_version):
                raise CandidatePreflightError(
                    f"ollama_version_too_old:{version}:minimum={minimum_ollama_version}"
                )

            tags_response = client.get("/api/tags")
            tags_response.raise_for_status()
            models = tags_response.json().get("models")
            if not isinstance(models, list):
                raise CandidatePreflightError("ollama_model_inventory_invalid")
    except CandidatePreflightError:
        raise
    except (httpx.HTTPError, ValueError) as exc:
        raise CandidatePreflightError("ollama_unavailable") from exc

    digest: str | None = None
    for model in models:
        if isinstance(model, dict) and model.get("name") == model_name:
            candidate_digest = model.get("digest")
            if isinstance(candidate_digest, str) and len(candidate_digest) >= 32:
                digest = candidate_digest
                break
    if digest is None:
        raise CandidatePreflightError(f"candidate_model_missing_or_unpinned:{model_name}")

    result = OllamaClient(candidate_settings).generate_json(
        system_prompt=(
            "Return the schema exactly. This is a synthetic local readiness probe, not health data."
        ),
        user_content="SYNTHETIC-DO-NOT-USE-REAL-DATA: return status ready.",
        json_schema=_PROBE_SCHEMA,
        model_name=model_name,
        max_output_tokens=QWEN38_MAX_OUTPUT_TOKENS,
        context_window=QWEN38_CONTEXT_WINDOW,
        read_timeout_s=max(candidate_settings.ollama_read_timeout_s, 120.0),
    )
    if not result.ok or result.data != {"status": "ready"}:
        reason = result.outcome.value if not result.ok else "schema_value_mismatch"
        raise CandidatePreflightError(f"candidate_structured_output_failed:{reason}")

    return CandidatePreflightReport(
        generated_at=datetime.now(UTC),
        base_url=candidate_settings.ollama_base_url,
        ollama_version=version,
        minimum_ollama_version=minimum_ollama_version,
        model_name=model_name,
        model_digest=digest,
        thinking_enabled=False,
        context_window=QWEN38_CONTEXT_WINDOW,
        structured_output=True,
        latency_ms=result.latency_ms or 0,
    )
