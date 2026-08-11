"""Verify the checked-in generated-analysis safety regression baseline."""

from __future__ import annotations

import argparse
import json
from datetime import UTC, datetime
from pathlib import Path

from healthcurve.ai.analysis import ANALYSIS_SCHEMA, SYSTEM_PROMPT
from healthcurve.ai.analysis_evaluation import (
    AnalysisEvaluationReport,
    AnalysisPrediction,
    load_analysis_gold,
    load_analysis_report,
    render_analysis_report,
    verify_analysis_report,
)
from healthcurve.ai.ollama import OllamaClient

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals" / "analysis" / "gold-v1.json"
BASELINE = ROOT / "evals" / "analysis" / "baseline-synthetic-validator.json"


def check() -> int:
    gold = load_analysis_gold(GOLD)
    report = load_analysis_report(BASELINE)
    summary = verify_analysis_report(gold, report)
    print(f"gold={gold.version} prompt={report.prompt_version} model={report.model_name}")
    for field, score in summary.scores.items():
        print(f"{field}: {score:.3f} (minimum {summary.thresholds[field]:.3f})")
    for failure in summary.failures:
        print(f"FAIL: {failure}")
    return 0 if summary.passed else 1


def record() -> int:
    gold = load_analysis_gold(GOLD)
    client = OllamaClient()
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
        predictions.append(AnalysisPrediction(id=case.id, response=result.data))
    report = AnalysisEvaluationReport(
        gold_set_version=gold.version,
        prompt_version=gold.prompt_version,
        model_name=identity.name,
        model_digest=identity.digest,
        generated_at=datetime.now(UTC),
        predictions=predictions,
    )
    summary = verify_analysis_report(gold, report)
    if not summary.passed:
        for failure in summary.failures:
            print(f"FAIL: {failure}")
        return 1
    BASELINE.write_text(render_analysis_report(report), encoding="utf-8")
    print(f"recorded {len(predictions)} safe cases for {identity.name}@{identity.digest}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--record", action="store_true")
    args = parser.parse_args()
    return record() if args.record else check()


if __name__ == "__main__":
    raise SystemExit(main())
