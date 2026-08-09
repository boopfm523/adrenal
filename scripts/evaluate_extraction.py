"""Record or verify the synthetic extraction regression baseline."""

from __future__ import annotations

import argparse
import sys
from datetime import UTC, datetime
from pathlib import Path

from healthcurve.ai.evaluation import (
    CasePrediction,
    EvaluationError,
    EvaluationReport,
    GoldSet,
    load_gold_set,
    load_report,
    prediction_from_response,
    render_report,
    stability_score,
    verify_report,
)
from healthcurve.ai.extraction import (
    CANDIDATE_JSON_SCHEMA,
    PROMPT_VERSION,
    SYSTEM_PROMPT,
    build_user_content,
)
from healthcurve.ai.ollama import OllamaClient

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "evals" / "extraction" / "gold-v1.json"
DEFAULT_BASELINE = ROOT / "evals" / "extraction" / "baseline-qwen3-30b.json"


def record(gold_path: Path, baseline_path: Path) -> int:
    gold = load_gold_set(gold_path)
    if gold.prompt_version != PROMPT_VERSION:
        raise EvaluationError("prompt_version_mismatch")
    client = OllamaClient()
    identity = client.identity()
    if identity is None:
        raise EvaluationError("model_identity_unavailable")

    predictions = run_model(gold, client)
    report = EvaluationReport(
        gold_set_version=gold.version,
        prompt_version=PROMPT_VERSION,
        model_name=identity.name,
        model_digest=identity.digest,
        generated_at=datetime.now(UTC),
        predictions=predictions,
    )
    summary = verify_report(gold, report)
    baseline_path.write_text(render_report(report), encoding="utf-8")
    _print_summary(report, summary.scores, summary.failures)
    return 0 if summary.passed else 1


def run_model(gold: GoldSet, client: OllamaClient) -> list[CasePrediction]:
    """Run every case once; stability uses this exact same call path."""
    predictions: list[CasePrediction] = []
    for case in gold.cases:
        result = client.generate_json(
            system_prompt=SYSTEM_PROMPT,
            user_content=build_user_content(
                case.message, gold.known_medications, case.timezone, case.now
            ),
            json_schema=CANDIDATE_JSON_SCHEMA,
        )
        if not result.ok or result.data is None:
            raise EvaluationError(f"model_run_failed:{case.id}:{result.outcome.value}")
        predictions.append(prediction_from_response(case.id, result.data))

    return predictions


def stability(gold_path: Path, runs: int) -> int:
    if runs < 2:
        raise EvaluationError("stability_runs_insufficient")
    gold = load_gold_set(gold_path)
    if gold.prompt_version != PROMPT_VERSION:
        raise EvaluationError("prompt_version_mismatch")
    client = OllamaClient()
    identity = client.identity()
    if identity is None:
        raise EvaluationError("model_identity_unavailable")
    summary = stability_score(gold, [run_model(gold, client) for _ in range(runs)])
    print(f"gold={gold.version} prompt={PROMPT_VERSION}")
    print(f"model={identity.name} digest={identity.digest} runs={runs}")
    for field, value in sorted(summary.scores.items()):
        print(f"stability.{field}: {value:.3f}")
    for failure in summary.failures:
        print(f"FAIL: {failure}", file=sys.stderr)
    return 0 if summary.passed else 1


def check(gold_path: Path, baseline_path: Path) -> int:
    gold = load_gold_set(gold_path)
    report = load_report(baseline_path)
    summary = verify_report(gold, report)
    _print_summary(report, summary.scores, summary.failures)
    return 0 if summary.passed else 1


def _print_summary(report: EvaluationReport, scores: dict[str, float], failures: list[str]) -> None:
    print(f"gold={report.gold_set_version} prompt={report.prompt_version}")
    print(f"model={report.model_name} digest={report.model_digest}")
    for field, value in sorted(scores.items()):
        print(f"{field}: {value:.3f}")
    for failure in failures:
        print(f"FAIL: {failure}", file=sys.stderr)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    parser.add_argument("--baseline", type=Path, default=DEFAULT_BASELINE)
    parser.add_argument(
        "--record", action="store_true", help="Run local Ollama and replace baseline"
    )
    parser.add_argument(
        "--stability-runs",
        type=int,
        default=0,
        help="Run every gold case N times and report per-field repeatability",
    )
    args = parser.parse_args()
    try:
        if args.record and args.stability_runs:
            raise EvaluationError("evaluation_mode_conflict")
        if args.stability_runs:
            return stability(args.gold, args.stability_runs)
        return record(args.gold, args.baseline) if args.record else check(args.gold, args.baseline)
    except (EvaluationError, OSError, ValueError) as exc:
        print(f"evaluation failed: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
