"""Deterministic safety and regression gates for generated analysis."""

from __future__ import annotations

import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest
from pydantic import ValidationError

from healthcurve.ai.analysis import (
    AnalysisOutcome,
    AnalysisResponse,
    AnalysisValidationError,
    canonicalize_safety_fields,
    generate_analysis,
    validate_response,
)
from healthcurve.ai.analysis_evaluation import (
    AnalysisEvaluationReport,
    load_analysis_gold,
    load_analysis_report,
    verify_analysis_report,
)
from healthcurve.ai.evaluation import EvaluationError
from healthcurve.ai.models import AnalysisType
from healthcurve.ai.ollama import ModelIdentity, ModelOutcome, ModelResult, OllamaClient
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
def test_analysis_requires_explicit_missingness_and_correlation_caution() -> None:
    contradictory = response().model_copy(update={"missingness": "none identified"})
    with pytest.raises(AnalysisValidationError, match="explicitly disclose missing data"):
        validate_response(
            contradictory,
            source_record_ids=[SOURCE],
            computed_inputs={"total_mg": "15.0000", "missing_records": 2},
        )

    without_caution = response().model_copy(update={"correlation_caution": "none"})
    with pytest.raises(AnalysisValidationError, match="correlation or causation caution"):
        validate_response(
            without_caution,
            source_record_ids=[SOURCE],
            computed_inputs={"total_mg": "15.0000", "missing_records": 0},
        )

    with pytest.raises(AnalysisValidationError, match="name every missing domain"):
        validate_response(
            response(),
            source_record_ids=[SOURCE],
            computed_inputs={"total_mg": "15.0000", "missing_domains": ["garmin_sleep"]},
        )
    validate_response(
        response().model_copy(update={"missingness": "Garmin sleep is missing."}),
        source_record_ids=[SOURCE],
        computed_inputs={"total_mg": "15.0000", "missing_domains": ["garmin_sleep"]},
    )


@pytest.mark.safety("SAFE-20")
def test_day_safety_fields_are_derived_from_deterministic_inputs() -> None:
    inputs: dict[str, object] = {
        "total_mg": "15.0000",
        "missing_records": 2,
        "missing_domains": ["garmin_sleep", "labs"],
    }
    claim = (
        response()
        .claims[0]
        .model_copy(update={"source_record_ids": [SOURCE, SOURCE], "numeric_values": []})
    )
    unsafe_paraphrase = response().model_copy(
        update={
            "claims": [claim],
            "missingness": "none identified",
            "correlation_caution": "none",
        }
    )

    checked = canonicalize_safety_fields(unsafe_paraphrase, inputs)

    assert "garmin sleep" in checked.missingness
    assert "labs" in checked.missingness
    assert "2" in checked.missingness
    assert "causation or diagnosis" in checked.correlation_caution
    assert checked.claims[0].source_record_ids == [SOURCE]
    assert checked.claims[0].numeric_values == ["15"]
    validate_response(checked, source_record_ids=[SOURCE], computed_inputs=inputs)


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
    assert any("cited-deterministic-total: observed=invalid" in item for item in summary.failures)


def test_analysis_gate_rejects_prompt_gold_or_model_provenance_drift() -> None:
    gold = load_analysis_gold(GOLD)
    report = load_analysis_report(BASELINE)
    with pytest.raises(EvaluationError, match="analysis_prompt_version_mismatch"):
        verify_analysis_report(gold, report.model_copy(update={"prompt_version": "changed"}))
    with pytest.raises(EvaluationError, match="analysis_gold_set_version_mismatch"):
        verify_analysis_report(gold, report.model_copy(update={"gold_set_version": "changed"}))
    with pytest.raises(EvaluationError, match="analysis_model_identity_missing"):
        verify_analysis_report(gold, report.model_copy(update={"model_digest": "changed"}))


def test_analysis_timeout_is_a_distinct_safe_result_without_a_write() -> None:
    session = Mock()
    model = Mock(spec=OllamaClient)
    model.generate_json.return_value = ModelResult(
        outcome=ModelOutcome.TIMEOUT,
        detail="synthetic timeout detail",
    )

    result = generate_analysis(
        session,
        owner_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        analysis_type=AnalysisType.DAILY_SUMMARY,
        source_record_ids=[SOURCE],
        computed_inputs={"missing_domains": ["labs"]},
        client=model,
    )

    assert result.outcome is AnalysisOutcome.MODEL_TIMEOUT
    assert result.analysis is None
    session.add.assert_not_called()


def test_analysis_malformed_model_response_is_distinct_without_a_write() -> None:
    session = Mock()
    model = Mock(spec=OllamaClient)
    model.generate_json.return_value = ModelResult(
        outcome=ModelOutcome.INVALID_JSON,
        detail="synthetic malformed response detail",
    )

    result = generate_analysis(
        session,
        owner_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        analysis_type=AnalysisType.DAILY_SUMMARY,
        source_record_ids=[SOURCE],
        computed_inputs={"missing_domains": ["labs"]},
        client=model,
    )

    assert result.outcome is AnalysisOutcome.MODEL_INVALID_RESPONSE
    assert result.analysis is None
    session.add.assert_not_called()


def test_analysis_forwards_bounded_generation_options() -> None:
    session = Mock()
    model = Mock(spec=OllamaClient)
    model.generate_json.return_value = ModelResult(
        outcome=ModelOutcome.TIMEOUT,
        detail="synthetic timeout detail",
    )

    generate_analysis(
        session,
        owner_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        analysis_type=AnalysisType.DAILY_SUMMARY,
        source_record_ids=[SOURCE],
        computed_inputs={"missing_domains": ["labs"]},
        client=model,
        max_output_tokens=1024,
        context_window=16_384,
        read_timeout_s=120.0,
    )

    assert model.generate_json.call_args.kwargs["max_output_tokens"] == 1024
    assert model.generate_json.call_args.kwargs["context_window"] == 16_384
    assert model.generate_json.call_args.kwargs["read_timeout_s"] == 120.0


def test_analysis_resolves_missing_chat_digest_from_local_inventory() -> None:
    session = Mock()
    model = Mock(spec=OllamaClient)
    model.generate_json.return_value = ModelResult(
        outcome=ModelOutcome.OK,
        data=response().model_dump(mode="json"),
        model_name="synthetic-model:latest",
    )
    model.identity.return_value = ModelIdentity(name="synthetic-model:latest", digest="a" * 64)

    result = generate_analysis(
        session,
        owner_id=uuid.UUID("00000000-0000-4000-8000-000000000001"),
        analysis_type=AnalysisType.DAILY_SUMMARY,
        source_record_ids=[SOURCE],
        computed_inputs={"total_mg": "15.0000", "missing_records": 0},
        client=model,
    )

    assert result.outcome is AnalysisOutcome.CREATED
    assert result.analysis is not None
    assert result.analysis.model_digest == "a" * 64
    model.identity.assert_called_once_with("synthetic-model:latest")
