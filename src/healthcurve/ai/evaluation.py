"""Versioned, synthetic regression evaluation for extraction models."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, field_validator

from healthcurve.ai.extraction import PROMPT_VERSION, ExtractionResponse


class EvaluationError(RuntimeError):
    pass


class GoldCandidate(BaseModel):
    fields: dict[str, str | int | bool | None]


class GoldCase(BaseModel):
    id: str
    synthetic_marker: str
    message: str
    timezone: str
    now: datetime
    expected: list[GoldCandidate]

    @field_validator("now")
    @classmethod
    def aware_time(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("gold case time must include an offset")
        return value


class GoldSet(BaseModel):
    version: str
    prompt_version: str
    synthetic_marker: str
    known_medications: list[str]
    thresholds: dict[str, float]
    cases: list[GoldCase]


class CasePrediction(BaseModel):
    id: str
    candidates: list[dict[str, Any]]


class EvaluationReport(BaseModel):
    gold_set_version: str
    prompt_version: str
    model_name: str
    model_digest: str
    generated_at: datetime
    predictions: list[CasePrediction]


class EvaluationSummary(BaseModel):
    scores: dict[str, float]
    thresholds: dict[str, float]
    failures: list[str] = Field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.failures


def load_gold_set(path: Path) -> GoldSet:
    return GoldSet.model_validate_json(path.read_text(encoding="utf-8"))


def load_report(path: Path) -> EvaluationReport:
    return EvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))


def score(gold: GoldSet, predictions: Sequence[CasePrediction]) -> EvaluationSummary:
    """Compute exact per-field accuracy; missing/extra candidates cannot disappear."""
    by_id = {prediction.id: prediction for prediction in predictions}
    if len(by_id) != len(predictions):
        raise EvaluationError("duplicate_prediction_id")
    expected_ids = {case.id for case in gold.cases}
    if set(by_id) != expected_ids:
        raise EvaluationError("prediction_case_set_mismatch")

    correct: dict[str, int] = {"candidate_count": 0}
    total: dict[str, int] = {"candidate_count": len(gold.cases)}
    for case in gold.cases:
        actual = by_id[case.id].candidates
        if len(actual) == len(case.expected):
            correct["candidate_count"] += 1
        for index, expected_candidate in enumerate(case.expected):
            candidate = actual[index] if index < len(actual) else {}
            for field, expected_value in expected_candidate.fields.items():
                total[field] = total.get(field, 0) + 1
                correct[field] = correct.get(field, 0) + int(candidate.get(field) == expected_value)

    scores = {field: correct.get(field, 0) / count for field, count in sorted(total.items())}
    unknown_thresholds = set(gold.thresholds) - set(scores)
    if unknown_thresholds:
        raise EvaluationError("threshold_without_observations")
    failures = [
        f"{field}={scores[field]:.3f} below {threshold:.3f}"
        for field, threshold in sorted(gold.thresholds.items())
        if scores[field] < threshold
    ]
    return EvaluationSummary(scores=scores, thresholds=gold.thresholds, failures=failures)


def verify_report(gold: GoldSet, report: EvaluationReport) -> EvaluationSummary:
    if gold.prompt_version != PROMPT_VERSION or report.prompt_version != PROMPT_VERSION:
        raise EvaluationError("prompt_version_mismatch")
    if report.gold_set_version != gold.version:
        raise EvaluationError("gold_set_version_mismatch")
    if not report.model_name or len(report.model_digest) < 32:
        raise EvaluationError("model_identity_missing")
    return score(gold, report.predictions)


def prediction_from_response(case_id: str, data: Mapping[str, Any]) -> CasePrediction:
    parsed = ExtractionResponse.model_validate(data)
    return CasePrediction(id=case_id, candidates=[item.model_dump() for item in parsed.candidates])


def render_report(report: EvaluationReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
