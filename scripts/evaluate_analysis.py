"""Verify the checked-in generated-analysis safety regression baseline."""

from __future__ import annotations

import argparse
import json
import sys
from datetime import UTC, datetime
from pathlib import Path

from healthcurve.ai.analysis import (
    ANALYSIS_SCHEMA,
    SYSTEM_PROMPT,
    AnalysisResponse,
    canonicalize_safety_fields,
)
from healthcurve.ai.analysis_evaluation import (
    AnalysisEvaluationReport,
    AnalysisPrediction,
    load_analysis_gold,
    load_analysis_report,
    render_analysis_report,
    verify_analysis_report,
)
from healthcurve.ai.evaluation import EvaluationError
from healthcurve.ai.ollama import OllamaClient
from healthcurve.config import Settings

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals" / "analysis" / "gold-v1.json"
BASELINE = ROOT / "evals" / "analysis" / "baseline-synthetic-validator.json"


def check(gold_path: Path = GOLD, baseline_path: Path = BASELINE) -> int:
    gold = load_analysis_gold(gold_path)
    report = load_analysis_report(baseline_path)
    summary = verify_analysis_report(gold, report)
    print(f"gold={gold.version} prompt={report.prompt_version} model={report.model_name}")
    for field, score in summary.scores.items():
        print(f"{field}: {score:.3f} (minimum {summary.thresholds[field]:.3f})")
    for failure in summary.failures:
        print(f"FAIL: {failure}")
    return 0 if summary.passed else 1


def record(
    gold_path: Path = GOLD,
    baseline_path: Path = BASELINE,
    model_name: str | None = None,
    settings: Settings | None = None,
) -> int:
    if model_name is not None and baseline_path.resolve() == BASELINE.resolve():
        raise EvaluationError("candidate_output_path_required")
    gold = load_analysis_gold(gold_path)
    settings = settings or Settings()
    if model_name is not None:
        settings = settings.model_copy(
            update={"ollama_model": model_name, "ollama_thinking": False}
        )
    client = OllamaClient(settings)
    identity = client.identity()
    if identity is None:
        print("FAIL: configured analysis model is unavailable or has no immutable digest")
        return 1
    predictions: list[AnalysisPrediction] = []
    for case in gold.cases:
        result = client.generate_json(
            system_prompt=SYSTEM_PROMPT,
            user_content=json.dumps(
                {
                    "request": case.request,
                    "source_record_ids": case.source_record_ids,
                    "computed_inputs": case.computed_inputs,
                },
                sort_keys=True,
                separators=(",", ":"),
            ),
            json_schema=ANALYSIS_SCHEMA,
            temperature=0.0,
        )
        if not result.ok or result.data is None:
            print(f"FAIL: {case.id}: {result.outcome.value}: {result.detail or 'no detail'}")
            return 1
        response = canonicalize_safety_fields(
            AnalysisResponse.model_validate(result.data), case.computed_inputs
        )
        predictions.append(
            AnalysisPrediction(id=case.id, response=response.model_dump(mode="json"))
        )
    report = AnalysisEvaluationReport(
        gold_set_version=gold.version,
        prompt_version=gold.prompt_version,
        model_name=identity.name,
        model_digest=identity.digest,
        generated_at=datetime.now(UTC),
        predictions=predictions,
    )
    summary = verify_analysis_report(gold, report)
    if model_name is not None:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(render_analysis_report(report), encoding="utf-8")
    if not summary.passed:
        for failure in summary.failures:
            print(f"FAIL: {failure}")
        return 1
    if model_name is None:
        baseline_path.parent.mkdir(parents=True, exist_ok=True)
        baseline_path.write_text(render_analysis_report(report), encoding="utf-8")
    print(f"recorded {len(predictions)} safe cases for {identity.name}@{identity.digest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument("--gold", type=Path, default=GOLD)
    parser.add_argument("--baseline", type=Path, default=BASELINE)
    parser.add_argument(
        "--model",
        help="Evaluate an explicit local model without changing HealthCurve configuration",
    )
    args = parser.parse_args()
    try:
        if args.model and not args.record:
            raise EvaluationError("model_override_requires_model_run")
        if args.model and args.baseline.resolve() == BASELINE.resolve():
            raise EvaluationError("candidate_output_path_required")
        return (
            record(args.gold, args.baseline, args.model)
            if args.record
            else check(args.gold, args.baseline)
        )
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"analysis evaluation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
