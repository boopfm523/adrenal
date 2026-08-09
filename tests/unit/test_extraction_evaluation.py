"""Synthetic gold-set regression gate for local extraction."""

from __future__ import annotations

from pathlib import Path

import pytest

from healthcurve.ai.evaluation import (
    EvaluationError,
    EvaluationReport,
    load_gold_set,
    load_report,
    verify_report,
)
from healthcurve.ai.extraction import PROMPT_VERSION
from tests.fixtures.synthetic import SYNTHETIC_MARKER

ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "evals" / "extraction" / "gold-v1.json"
BASELINE_PATH = ROOT / "evals" / "extraction" / "baseline-qwen3-30b.json"


def test_checked_in_model_baseline_meets_every_field_threshold() -> None:
    gold = load_gold_set(GOLD_PATH)
    report = load_report(BASELINE_PATH)
    summary = verify_report(gold, report)
    assert summary.passed, summary.failures
    assert report.prompt_version == PROMPT_VERSION
    assert len(report.model_digest) == 64


def test_a_field_regression_fails_the_gate() -> None:
    gold = load_gold_set(GOLD_PATH)
    raw = load_report(BASELINE_PATH).model_dump(mode="json")
    for prediction in raw["predictions"]:
        for candidate in prediction["candidates"]:
            if candidate.get("amount") is not None:
                candidate["amount"] = "wrong"
    degraded = EvaluationReport.model_validate(raw)
    summary = verify_report(gold, degraded)
    assert not summary.passed
    assert any(failure.startswith("amount=") for failure in summary.failures)


def test_stale_prompt_or_gold_provenance_is_rejected() -> None:
    gold = load_gold_set(GOLD_PATH)
    report = load_report(BASELINE_PATH)
    with pytest.raises(EvaluationError, match="prompt_version_mismatch"):
        verify_report(gold, report.model_copy(update={"prompt_version": "stale"}))
    with pytest.raises(EvaluationError, match="gold_set_version_mismatch"):
        verify_report(gold, report.model_copy(update={"gold_set_version": "stale"}))


def test_gold_set_covers_required_synthetic_safety_cases() -> None:
    gold = load_gold_set(GOLD_PATH)
    required = {
        "relative-time",
        "overnight-event",
        "travel-timezone",
        "dst-ambiguous",
        "decimal-fraction",
        "self-correction",
        "multiple-events",
        "negated-dose",
        "hypothetical-dose",
        "prompt-injection",
    }
    assert {case.id for case in gold.cases} == required
    assert gold.synthetic_marker == SYNTHETIC_MARKER
    assert all(case.synthetic_marker == SYNTHETIC_MARKER for case in gold.cases)
