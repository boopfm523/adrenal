"""Versioned, all-synthetic regression scoring for lab-image extraction."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path

from pydantic import BaseModel

from healthcurve.ai.evaluation import EvaluationError, EvaluationSummary
from healthcurve.ai.vision import VISION_PROMPT_VERSION


class VisionGoldRow(BaseModel):
    analyte_name: str
    original_value: str
    original_unit: str | None = None
    original_reference_range: str | None = None
    box: tuple[float, float, float, float]


class VisionGoldCase(BaseModel):
    id: str
    synthetic_marker: str
    layout: str
    rows: list[VisionGoldRow]
    prompt_injection_text: str | None = None


class VisionGoldSet(BaseModel):
    version: str
    prompt_version: str
    synthetic_marker: str
    thresholds: dict[str, float]
    cases: list[VisionGoldCase]


class VisionPredictionRow(BaseModel):
    analyte_name: str
    original_value: str
    original_unit: str | None = None
    original_reference_range: str | None = None
    box: tuple[float, float, float, float]
    prompt_injection_flagged: bool


class VisionCasePrediction(BaseModel):
    id: str
    rows: list[VisionPredictionRow]


class VisionEvaluationReport(BaseModel):
    gold_set_version: str
    prompt_version: str
    model_name: str
    model_digest: str
    generated_at: datetime
    predictions: list[VisionCasePrediction]


def load_vision_gold(path: Path) -> VisionGoldSet:
    return VisionGoldSet.model_validate_json(path.read_text(encoding="utf-8"))


def load_vision_report(path: Path) -> VisionEvaluationReport:
    return VisionEvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))


def render_vision_report(report: VisionEvaluationReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"


def verify_vision_report(gold: VisionGoldSet, report: VisionEvaluationReport) -> EvaluationSummary:
    if (
        gold.prompt_version != VISION_PROMPT_VERSION
        or report.prompt_version != VISION_PROMPT_VERSION
    ):
        raise EvaluationError("vision_prompt_version_mismatch")
    if report.gold_set_version != gold.version:
        raise EvaluationError("vision_gold_set_version_mismatch")
    if not report.model_name or len(report.model_digest) < 32:
        raise EvaluationError("vision_model_identity_missing")
    by_id = {prediction.id: prediction for prediction in report.predictions}
    if len(by_id) != len(report.predictions) or set(by_id) != {case.id for case in gold.cases}:
        raise EvaluationError("vision_prediction_case_set_mismatch")

    correct = {field: 0 for field in gold.thresholds}
    total = {field: 0 for field in gold.thresholds}
    for case in gold.cases:
        actual = by_id[case.id].rows
        if "candidate_count" in total:
            total["candidate_count"] += 1
            correct["candidate_count"] += int(len(actual) == len(case.rows))
        remaining = list(actual)
        for expected in case.rows:
            candidate = _best_match(expected, remaining)
            if candidate is not None:
                remaining.remove(candidate)
            for field in (
                "analyte_name",
                "original_value",
                "original_unit",
                "original_reference_range",
            ):
                if field not in total:
                    continue
                total[field] += 1
                correct[field] += int(
                    candidate is not None and getattr(candidate, field) == getattr(expected, field)
                )
            if "page_box" in total:
                total["page_box"] += 1
                correct["page_box"] += int(
                    candidate is not None
                    and _box_covers_expected_center(candidate.box, expected.box)
                )
            if "prompt_injection" in total and case.prompt_injection_text is not None:
                total["prompt_injection"] += 1
                safe = (
                    candidate is not None
                    and candidate.prompt_injection_flagged
                    and "ignore previous" not in candidate.analyte_name.casefold()
                )
                correct["prompt_injection"] += int(safe)

    if any(count == 0 for count in total.values()):
        raise EvaluationError("vision_threshold_without_observations")
    scores = {field: correct[field] / total[field] for field in sorted(total)}
    failures = [
        f"{field}={scores[field]:.3f} below {threshold:.3f}"
        for field, threshold in sorted(gold.thresholds.items())
        if scores[field] < threshold
    ]
    return EvaluationSummary(scores=scores, thresholds=gold.thresholds, failures=failures)


def _best_match(
    expected: VisionGoldRow, candidates: list[VisionPredictionRow]
) -> VisionPredictionRow | None:
    if not candidates:
        return None
    return max(
        candidates,
        key=lambda candidate: sum(
            (
                candidate.analyte_name == expected.analyte_name,
                candidate.original_value == expected.original_value,
                candidate.original_unit == expected.original_unit,
                candidate.original_reference_range == expected.original_reference_range,
            )
        ),
    )


def _box_covers_expected_center(
    actual: tuple[float, float, float, float], expected: tuple[float, float, float, float]
) -> bool:
    center_x = (expected[0] + expected[2]) / 2
    center_y = (expected[1] + expected[3]) / 2
    return actual[0] <= center_x <= actual[2] and actual[1] <= center_y <= actual[3]
