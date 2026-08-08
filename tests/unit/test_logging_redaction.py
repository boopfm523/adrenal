"""SAFE-29: logs carry no health data or secrets.

The point of these tests is the *default*. A deny-list would let any newly added field
through until someone remembered to block it; the allow-list means a new field is
redacted until someone justifies it.
"""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest
import structlog

from healthcurve.logging import LOGGABLE_KEYS, REDACTED, configure_logging, redact_unlisted


def _redact(**fields: Any) -> dict[str, Any]:
    return dict(redact_unlisted(None, "info", dict(fields)))


@pytest.mark.safety("SAFE-29")
def test_unlisted_fields_are_redacted_by_default() -> None:
    out = _redact(event="dose recorded", medication="hydrocortisone", amount="15.0000")
    assert out["event"] == "dose recorded"
    assert out["medication"] == REDACTED
    assert out["amount"] == REDACTED


@pytest.mark.safety("SAFE-29")
def test_redaction_keeps_the_key_so_the_drop_is_visible() -> None:
    out = _redact(event="x", symptom="nausea")
    assert "symptom" in out
    assert out["symptom"] == REDACTED


@pytest.mark.safety("SAFE-29")
@pytest.mark.parametrize(
    "field",
    [
        "access_token",
        "refresh_token",
        "telegram_bot_token",
        "password",
        "api_key",
        "authorization",
    ],
)
def test_credentials_are_never_loggable(field: str) -> None:
    """Class C8 credentials: never logged in any form."""
    assert field not in LOGGABLE_KEYS
    assert _redact(event="sync", **{field: "s3cret"})[field] == REDACTED


@pytest.mark.safety("SAFE-29")
@pytest.mark.parametrize(
    "field",
    [
        "prompt",
        "completion",
        "raw_response",
        "message_text",
        "telegram_message",
        "diary_text",
        "note",
    ],
)
def test_model_io_and_free_text_are_never_loggable(field: str) -> None:
    """Class C9 model I/O contains health facts verbatim."""
    assert field not in LOGGABLE_KEYS
    assert _redact(event="extract", **{field: "took 15mg at 7am"})[field] == REDACTED


@pytest.mark.safety("SAFE-29")
@pytest.mark.parametrize(
    "field",
    ["latitude", "longitude", "coordinates", "lab_value", "analyte_value", "heart_rate"],
)
def test_location_lab_and_biometric_values_are_never_loggable(field: str) -> None:
    """Classes C4, C5, C6 -- values never logged, only provider and counts."""
    assert field not in LOGGABLE_KEYS
    assert _redact(event="enrich", **{field: "51.5074"})[field] == REDACTED


@pytest.mark.safety("SAFE-29")
def test_operational_metadata_survives_redaction() -> None:
    """Redaction must not make logs useless -- C0 operational context is kept."""
    out = _redact(
        event="garmin sync",
        correlation_id="c-1",
        provider="garmin",
        count=12,
        duration_ms=340,
        model_name="qwen3-coder",
        prompt_version="v3",
    )
    assert out["correlation_id"] == "c-1"
    assert out["provider"] == "garmin"
    assert out["count"] == 12
    assert out["duration_ms"] == 340
    assert out["model_name"] == "qwen3-coder"
    assert out["prompt_version"] == "v3"


#: Words that name a value rather than a piece of operational metadata. Matched
#: against underscore-separated tokens, not substrings -- "correlation_id" must not
#: trip on "lat", and "latency_ms" must not trip on "latitude".
FORBIDDEN_KEY_TOKENS = frozenset(
    {
        "token",
        "secret",
        "password",
        "credential",
        "prompt",
        "completion",
        "response",
        "text",
        "body",
        "payload",
        "amount",
        "dose",
        "medication",
        "symptom",
        "note",
        "diary",
        "latitude",
        "longitude",
        "coordinates",
        "value",
    }
)

#: Reviewed exceptions: the token appears, but the field names metadata *about* the
#: thing rather than the thing itself. Each entry is a deliberate privacy decision.
REVIEWED_KEY_EXCEPTIONS = frozenset(
    {
        "prompt_version",  # which prompt was used, never the prompt itself (C9)
    }
)


@pytest.mark.safety("SAFE-29")
def test_allowlist_contains_no_value_bearing_health_field() -> None:
    """Guard against the allow-list drifting into health data over time."""
    offenders = sorted(
        key
        for key in LOGGABLE_KEYS
        if key not in REVIEWED_KEY_EXCEPTIONS and FORBIDDEN_KEY_TOKENS & set(key.lower().split("_"))
    )
    assert not offenders, (
        f"allow-list contains value-bearing fields: {offenders}. "
        f"Either rename the field to carry metadata only, or add it to "
        f"REVIEWED_KEY_EXCEPTIONS with a justification against docs/threat-model.md."
    )


@pytest.mark.safety("SAFE-29")
def test_reviewed_exceptions_are_still_in_the_allowlist() -> None:
    """A stale exception would silently weaken the guard above."""
    stale = REVIEWED_KEY_EXCEPTIONS - LOGGABLE_KEYS
    assert not stale, f"exceptions granted for keys no longer logged: {sorted(stale)}"


@pytest.mark.safety("SAFE-29")
def test_end_to_end_pipeline_redacts(capsys: pytest.CaptureFixture[str]) -> None:
    """The configured pipeline, not just the processor, redacts."""
    configure_logging(json_output=True, level=logging.INFO)
    structlog.get_logger("test").info("dose recorded", medication="hydrocortisone", count=1)
    captured = capsys.readouterr().out.strip()
    payload = json.loads(captured)
    assert payload["medication"] == REDACTED
    assert payload["count"] == 1
    assert "hydrocortisone" not in captured
