from datetime import UTC, datetime
from pathlib import Path

from healthcurve.ai.vision_evaluation import (
    VisionCasePrediction,
    VisionEvaluationReport,
    VisionPredictionRow,
    load_vision_gold,
    verify_vision_report,
)

ROOT = Path(__file__).resolve().parents[2]
GOLD_PATH = ROOT / "evals" / "vision" / "gold-v1.json"


def _report() -> VisionEvaluationReport:
    gold = load_vision_gold(GOLD_PATH)
    predictions = []
    for case in gold.cases:
        predictions.append(
            VisionCasePrediction(
                id=case.id,
                rows=[
                    VisionPredictionRow(
                        analyte_name=row.analyte_name,
                        original_value=row.original_value,
                        original_unit=row.original_unit,
                        original_reference_range=row.original_reference_range,
                        box=row.box,
                        prompt_injection_flagged=case.prompt_injection_text is not None,
                    )
                    for row in case.rows
                ],
            )
        )
    return VisionEvaluationReport(
        gold_set_version=gold.version,
        prompt_version=gold.prompt_version,
        model_name="qwen3-vl:30b",
        model_digest="a" * 64,
        generated_at=datetime.now(UTC),
        predictions=predictions,
    )


def test_synthetic_vision_gold_covers_layouts_fields_boxes_and_injection() -> None:
    gold = load_vision_gold(GOLD_PATH)
    assert gold.synthetic_marker == "SYNTHETIC_TEST_DATA"
    assert {case.layout for case in gold.cases} == {"cards", "table"}
    assert any(case.prompt_injection_text for case in gold.cases)
    assert set(gold.thresholds) == {
        "analyte_name",
        "candidate_count",
        "original_reference_range",
        "original_unit",
        "original_value",
        "page_box",
        "prompt_injection",
    }


def test_exact_vision_baseline_passes_and_field_regression_fails() -> None:
    gold = load_vision_gold(GOLD_PATH)
    report = _report()
    assert verify_vision_report(gold, report).passed
    report.predictions[0].rows[0].original_value = "wrong"
    summary = verify_vision_report(gold, report)
    assert not summary.passed
    assert any(failure.startswith("original_value=") for failure in summary.failures)
