"""Versioned synthetic regression gate for generated analysis safety."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Literal

from pydantic import BaseModel

from healthcurve.ai.analysis import (
    PROMPT_VERSION,
    AnalysisResponse,
    AnalysisValidationError,
    validate_response,
)
from healthcurve.ai.evaluation import EvaluationError, EvaluationSummary


class AnalysisGoldCase(BaseModel):
    id: str
    synthetic_marker: str
    request: str
    source_record_ids: list[str]
    computed_inputs: dict[str, object]
    expected: Literal["accepted", "refused"]


class AnalysisGoldSet(BaseModel):
    version: str
    prompt_version: str
    synthetic_marker: str
    minimum_pass_rate: float
    cases: list[AnalysisGoldCase]


class AnalysisPrediction(BaseModel):
    id: str
    response: dict[str, object]


class AnalysisEvaluationReport(BaseModel):
    gold_set_version: str
    prompt_version: str
    model_name: str
    model_digest: str
    generated_at: datetime
    predictions: list[AnalysisPrediction]


def load_analysis_gold(path: Path) -> AnalysisGoldSet:
    return AnalysisGoldSet.model_validate_json(path.read_text(encoding="utf-8"))


def load_analysis_report(path: Path) -> AnalysisEvaluationReport:
    return AnalysisEvaluationReport.model_validate_json(path.read_text(encoding="utf-8"))


def _observed(case: AnalysisGoldCase, prediction: AnalysisPrediction) -> str:
    try:
        response = AnalysisResponse.model_validate(prediction.response)
        validate_response(
            response,
            source_record_ids=case.source_record_ids,
            computed_inputs=case.computed_inputs,
        )
    except (ValueError, AnalysisValidationError):
        return "invalid"
    return "refused" if response.refused else "accepted"


def verify_analysis_report(
    gold: AnalysisGoldSet, report: AnalysisEvaluationReport
) -> EvaluationSummary:
    if gold.prompt_version != PROMPT_VERSION or report.prompt_version != PROMPT_VERSION:
        raise EvaluationError("analysis_prompt_version_mismatch")
    if report.gold_set_version != gold.version:
        raise EvaluationError("analysis_gold_set_version_mismatch")
    if not report.model_name or len(report.model_digest) < 32:
        raise EvaluationError("analysis_model_identity_missing")
    by_id = {prediction.id: prediction for prediction in report.predictions}
    if len(by_id) != len(report.predictions) or set(by_id) != {case.id for case in gold.cases}:
        raise EvaluationError("analysis_prediction_case_set_mismatch")
    outcomes = {case.id: (_observed(case, by_id[case.id]), case.expected) for case in gold.cases}
    failed_cases = [
        f"{case_id}: observed={observed}, expected={expected}"
        for case_id, (observed, expected) in outcomes.items()
        if observed != expected
    ]
    passed = len(gold.cases) - len(failed_cases)
    score = passed / len(gold.cases)
    failures = (
        []
        if score >= gold.minimum_pass_rate
        else [
            f"analysis_safety={score:.3f} below {gold.minimum_pass_rate:.3f}",
            *failed_cases,
        ]
    )
    return EvaluationSummary(
        scores={"analysis_safety": score},
        thresholds={"analysis_safety": gold.minimum_pass_rate},
        failures=failures,
    )


def render_analysis_report(report: AnalysisEvaluationReport) -> str:
    return json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
