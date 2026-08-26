from __future__ import annotations

import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
from scripts import evaluate_analysis, evaluate_chatbot, evaluate_extraction

from healthcurve.ai.evaluation import load_gold_set
from healthcurve.ai.extraction import EXTRACTION_READ_TIMEOUT_SECONDS
from healthcurve.ai.ollama import ModelOutcome, ModelResult


@pytest.mark.parametrize(
    ("module", "expected"),
    [
        (evaluate_extraction, 2),
        (evaluate_chatbot, 1),
        (evaluate_analysis, 1),
    ],
)
def test_candidate_recording_requires_a_separate_output(
    monkeypatch: pytest.MonkeyPatch, module: ModuleType, expected: int
) -> None:
    monkeypatch.setattr(
        sys,
        "argv",
        [module.__name__, "--record", "--model", "qwen3.8:27b-q8_0"],
    )
    assert module.main() == expected  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "module",
    [evaluate_extraction, evaluate_chatbot, evaluate_analysis],
)
def test_candidate_model_and_output_are_forwarded(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, module: ModuleType
) -> None:
    output = tmp_path / "candidate.json"
    observed: dict[str, Any] = {}

    def fake_record(*args: Any) -> int:
        observed["args"] = args
        return 0

    monkeypatch.setattr(module, "record", fake_record)
    monkeypatch.setattr(
        sys,
        "argv",
        [
            module.__name__,
            "--record",
            "--model",
            "qwen3.8:27b-q8_0",
            "--baseline",
            str(output),
        ],
    )

    assert module.main() == 0  # type: ignore[attr-defined]
    forwarded: tuple[object, ...] = observed["args"]
    assert output in forwarded
    assert "qwen3.8:27b-q8_0" in forwarded


def test_extraction_evaluation_uses_the_production_timeout() -> None:
    calls: list[dict[str, Any]] = []

    class FakeClient:
        def generate_json(self, **kwargs: Any) -> ModelResult:
            calls.append(kwargs)
            return ModelResult(outcome=ModelOutcome.OK, data={"candidates": []})

    gold = load_gold_set(evaluate_extraction.DEFAULT_GOLD)
    predictions = evaluate_extraction.run_model(gold, FakeClient())  # type: ignore[arg-type]

    assert len(predictions) == len(gold.cases)
    assert calls
    assert all(call["read_timeout_s"] == EXTRACTION_READ_TIMEOUT_SECONDS for call in calls)
