"""Deterministic safety and regression gates for generated analysis."""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from healthcurve.ai.analysis import (
    AnalysisResponse,
    AnalysisValidationError,
    validate_response,
)
from healthcurve.ai.analysis_evaluation import (
    AnalysisEvaluationReport,
    load_analysis_gold,
    load_analysis_report,
    verify_analysis_report,
)
from healthcurve.ai.evaluation import EvaluationError
from healthcurve.api.schemas import DoseIn, RegimenApprovalIn, RegimenVersionIn
from tests.fixtures.synthetic import SYNTHETIC_MARKER

ROOT = Path(__file__).resolve().parents[2]
GOLD = ROOT / "evals" / "analysis" / "gold-v1.json"
BASELINE = ROOT / "evals" / "analysis" / "baseline-synthetic-validator.json"
SOURCE = "00000000-0000-4000-8000-000000000201"


def response(*, text: str = "The synthetic total was 15 mg.") -> AnalysisResponse:
    return AnalysisResponse.model_validate(
        {
            "refused": False,
            "refusal_reason": None,
            "claims": [
                {
                    "text": text,
                    "source_record_ids": [SOURCE],
                    "numeric_values": ["15.0000"],
                }
            ],
            "missingness": "The input reports 0 missing records.",
            "correlation_caution": "This description does not establish causation.",
        }
    )


@pytest.mark.safety("SAFE-05")
def test_analysis_requires_manifest_citations_and_complete_provenance_shape() -> None:
    validate_response(
        response(),
        source_record_ids=[SOURCE],
        computed_inputs={"total_mg": "15.0000", "missing_records": 0},
    )
    with pytest.raises(AnalysisValidationError, match="outside its manifest"):
        validate_response(
            response().model_copy(
                update={
                    "claims": [
                        response()
                        .claims[0]
                        .model_copy(update={"source_record_ids": ["unlisted-source"]})
                    ]
                }
            ),
            source_record_ids=[SOURCE],
            computed_inputs={"total_mg": "15.0000", "missing_records": 0},
        )


@pytest.mark.safety("SAFE-20")
def test_analysis_rejects_every_number_absent_from_computed_input() -> None:
    with pytest.raises(AnalysisValidationError, match="absent from computed input"):
        validate_response(
            response(text="The synthetic total was 25 mg."),
            source_record_ids=[SOURCE],
            computed_inputs={"total_mg": "15.0000", "missing_records": 0},
        )
    unsafe_missingness = response().model_copy(update={"missingness": "There were 7 gaps."})
    with pytest.raises(AnalysisValidationError, match="absent from computed input"):
        validate_response(
            unsafe_missingness,
            source_record_ids=[SOURCE],
            computed_inputs={"total_mg": "15.0000", "missing_records": 0},
        )


@pytest.mark.safety("SAFE-17")
def test_analysis_schema_and_validator_reject_medication_guidance() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        AnalysisResponse.model_validate(
            {
                **response().model_dump(),
                "recommended_dose": "20 mg",
            }
        )


@pytest.mark.safety("SAFE-18")
def test_fact_and_plan_write_contracts_cannot_promote_ai_analysis() -> None:
    for schema in (DoseIn, RegimenVersionIn, RegimenApprovalIn):
        fields = set(schema.model_fields)
        assert "ai_analysis_id" not in fields
        assert "analysis_id" not in fields
        assert "source_analysis_id" not in fields
    with pytest.raises(AnalysisValidationError, match="medication guidance"):
        validate_response(
            response(text="You should increase the dose to 15 mg."),
            source_record_ids=[SOURCE],
            computed_inputs={"total_mg": "15.0000", "missing_records": 0},
        )


def test_analysis_gold_baseline_passes_and_degradation_fails() -> None:
    gold = load_analysis_gold(GOLD)
    report = load_analysis_report(BASELINE)
    assert verify_analysis_report(gold, report).passed
    assert gold.synthetic_marker == SYNTHETIC_MARKER
    assert all(case.synthetic_marker == SYNTHETIC_MARKER for case in gold.cases)

    raw = report.model_dump(mode="json")
    raw["predictions"][0]["response"]["claims"][0]["text"] = (
        "The synthetic recorded total was 999 mg."
    )
    degraded = AnalysisEvaluationReport.model_validate(raw)
    summary = verify_analysis_report(gold, degraded)
    assert not summary.passed


def test_analysis_gate_rejects_prompt_gold_or_model_provenance_drift() -> None:
    gold = load_analysis_gold(GOLD)
    report = load_analysis_report(BASELINE)
    with pytest.raises(EvaluationError, match="analysis_prompt_version_mismatch"):
        verify_analysis_report(gold, report.model_copy(update={"prompt_version": "changed"}))
    with pytest.raises(EvaluationError, match="analysis_gold_set_version_mismatch"):
        verify_analysis_report(gold, report.model_copy(update={"gold_set_version": "changed"}))
    with pytest.raises(EvaluationError, match="analysis_model_identity_missing"):
        verify_analysis_report(gold, report.model_copy(update={"model_digest": "changed"}))
