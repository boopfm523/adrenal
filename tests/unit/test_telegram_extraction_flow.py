"""Proof boundary for free-text Telegram capture (hc-34v.10).

The live model smoke is an operator check because CI has no Ollama. These tests pin
what happens on either side of that call: valid output creates only a draft through
the restricted session; every required failure is visible and writes nothing.
"""

from __future__ import annotations

import uuid
from contextlib import nullcontext
from datetime import UTC, datetime
from typing import Any, cast, override
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from healthcurve.ai.models import ExtractionDraft
from healthcurve.ai.ollama import ModelOutcome, ModelResult, OllamaClient
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import handlers
from healthcurve.operations.rate_limit import (
    RateLimiter,
    RateLimitExceeded,
    RateLimitPolicy,
    RateLimitResult,
)
from tests.fixtures.synthetic import SYNTHETIC_MARKER

OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000001")
DRAFT_ID = uuid.UUID("00000000-0000-4000-8000-000000000002")
NOW = datetime(2026, 8, 9, 17, 0, tzinfo=UTC)


class StubOllamaClient(OllamaClient):
    def __init__(self, result: ModelResult) -> None:
        self.result = result

    @override
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
    ) -> ModelResult:
        del (
            system_prompt,
            user_content,
            json_schema,
            temperature,
            model_name,
            images,
            max_output_tokens,
            context_window,
        )
        return self.result


def _owner() -> Owner:
    return Owner(
        id=OWNER_ID,
        email="owner@example.test",
        password_hash="synthetic-not-a-real-hash",
        default_timezone="UTC",
    )


def _empty_session() -> tuple[Session, MagicMock]:
    mocked = MagicMock(spec=Session)
    mocked.scalars.return_value = []
    return cast(Session, mocked), mocked


@pytest.mark.parametrize(
    "outcome",
    [ModelOutcome.INVALID_JSON, ModelOutcome.TIMEOUT, ModelOutcome.UNAVAILABLE],
)
def test_model_failure_is_visible_and_writes_nothing(outcome: ModelOutcome) -> None:
    session, mocked = _empty_session()
    client = StubOllamaClient(ModelResult(outcome=outcome, model_name="qwen3:30b"))

    reply = handlers.handle_message(
        session,
        _owner(),
        text=f"{SYNTHETIC_MARKER}: fictional diary event just now.",
        client=client,
        now=NOW,
    )

    assert "language model is unavailable" in reply.text
    assert "Nothing was recorded" in reply.text
    assert reply.draft_id is None
    mocked.add.assert_not_called()


def test_model_rate_limit_is_visible_and_deterministic_commands_remain_available() -> None:
    session, mocked = _empty_session()
    client = MagicMock(spec=OllamaClient)
    limiter = MagicMock(spec=RateLimiter)
    limiter.check.side_effect = RateLimitExceeded(RateLimitResult(30, 0, 47))

    reply = handlers.handle_message(
        session,
        _owner(),
        text=f"{SYNTHETIC_MARKER}: fictional diary event just now.",
        client=client,
        limiter=limiter,
        model_policy=RateLimitPolicy(30, 3600),
        now=NOW,
    )

    assert "automatic-reading limit" in reply.text
    assert "Nothing was recorded" in reply.text
    assert "47 seconds" in reply.text
    assert "/dose" in reply.text
    client.generate_json.assert_not_called()
    mocked.add.assert_not_called()


def test_schema_valid_output_creates_only_a_pending_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, privileged = _empty_session()
    restricted = MagicMock(spec=Session)
    restricted.scalar.return_value = None
    restricted.begin.return_value = nullcontext()

    def assign_database_default() -> None:
        draft = restricted.add.call_args.args[0]
        assert isinstance(draft, ExtractionDraft)
        draft.id = DRAFT_ID

    restricted.flush.side_effect = assign_database_default
    factory = MagicMock(return_value=nullcontext(cast(Session, restricted)))
    monkeypatch.setattr(handlers, "get_ai_session_factory", lambda: factory)

    client = StubOllamaClient(
        ModelResult(
            outcome=ModelOutcome.OK,
            model_name="qwen3:30b",
            data={
                "candidates": [
                    {
                        "type": "diary",
                        "text": f"{SYNTHETIC_MARKER}: fictional garden walk",
                        "local_time": "just now",
                        "negated": False,
                        "hypothetical": False,
                        "confidence": 0.95,
                    }
                ]
            },
        )
    )

    reply = handlers.handle_message(
        session,
        _owner(),
        text=f"{SYNTHETIC_MARKER}: fictional garden walk just now.",
        message_id="synthetic-message-1",
        client=client,
        now=NOW,
    )

    assert reply.draft_id == DRAFT_ID
    assert "Nothing is recorded yet" in reply.text
    assert "language model is unavailable" not in reply.text
    privileged.add.assert_not_called()
    restricted.add.assert_called_once()
    stored = restricted.add.call_args.args[0]
    assert isinstance(stored, ExtractionDraft)
    assert stored.model_name == "qwen3:30b"
    assert stored.state.value == "pending"
    assert stored.raw_text is not None and SYNTHETIC_MARKER in stored.raw_text


def test_natural_language_vitals_create_only_confirmation_required_draft(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session, privileged = _empty_session()
    restricted = MagicMock(spec=Session)
    restricted.scalar.return_value = None
    restricted.begin.return_value = nullcontext()

    def assign_database_default() -> None:
        draft = restricted.add.call_args.args[0]
        assert isinstance(draft, ExtractionDraft)
        draft.id = DRAFT_ID

    restricted.flush.side_effect = assign_database_default
    factory = MagicMock(return_value=nullcontext(cast(Session, restricted)))
    monkeypatch.setattr(handlers, "get_ai_session_factory", lambda: factory)
    client = StubOllamaClient(
        ModelResult(
            outcome=ModelOutcome.OK,
            model_name="qwen3:30b",
            data={
                "candidates": [
                    {
                        "type": "blood_pressure",
                        "systolic_mmhg": 40,
                        "diastolic_mmhg": 250,
                        "pulse_bpm": 1,
                        "local_time": "2026-08-09T08:15:00",
                        "negated": False,
                        "hypothetical": False,
                        "confidence": 0.99,
                    },
                    {
                        "type": "weight",
                        "amount": "180",
                        "unit": "lb",
                        "local_time": "2026-08-09T08:20:00",
                        "negated": False,
                        "hypothetical": False,
                        "confidence": 0.99,
                    },
                    {
                        "type": "temperature",
                        "temperature_value": "38",
                        "temperature_unit": "c",
                        "local_time": "2026-08-09T08:25:00",
                        "negated": False,
                        "hypothetical": False,
                        "confidence": 0.99,
                    },
                ]
            },
        )
    )

    reply = handlers.handle_message(
        session,
        _owner(),
        text=(
            f"{SYNTHETIC_MARKER}: blood pressure 40/250 pulse 1, weight 180 lb, "
            "and temperature 38 C"
        ),
        client=client,
        now=NOW,
    )

    assert reply.draft_id == DRAFT_ID
    assert "Nothing is recorded yet" in reply.text
    assert "Blood pressure: 40/250 mmHg, pulse 1 bpm" in reply.text
    assert "Weight: 180.0 lb" in reply.text
    assert "Temperature: 100.4 °F (38.0 °C)" in reply.text
    privileged.add.assert_not_called()
    stored = restricted.add.call_args.args[0]
    assert isinstance(stored, ExtractionDraft)
    assert {candidate["type"] for candidate in stored.candidates} == {
        "blood_pressure",
        "temperature",
        "weight",
    }
