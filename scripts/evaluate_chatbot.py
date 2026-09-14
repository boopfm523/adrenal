"""Verify or record the selected private-model chatbot regression baseline (ADR-0036).

Every case is synthetic. Tool results are canned per case and tool name, so the suite
measures the local model's tool choice, numeric discipline, refusals, and injection
resistance through the real orchestration and validation code without a database.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from healthcurve.ai.evaluation import EvaluationError
from healthcurve.ai.ollama import OllamaClient
from healthcurve.analysis.catalog import CATALOG_VERSION
from healthcurve.analysis.tools import (
    AnalysisAccess,
    ToolOutput,
    execute_analysis_tool,
    tool_definitions,
)
from healthcurve.chat.models import ChatRole
from healthcurve.chat.orchestration import PROMPT_VERSION, SCHEMA_VERSION, run
from healthcurve.chat.service import BoundedConversationContext, ContextTurn
from healthcurve.config import Settings

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals" / "chatbot" / "gold-v3.json"
BASELINE = ROOT / "evals" / "chatbot" / "baseline-v3.json"
SYNTHETIC_MARKER = "SYNTHETIC-DO-NOT-USE-REAL-DATA"
CURRENT_LOCAL_DATETIME = datetime.fromisoformat("2026-08-15T16:00:00-04:00")
TIMEZONE = "America/New_York"
_DIGIT_GROUPING = re.compile(r"(?<=\d),(?=\d{3}(?!\d))")


class GoldCase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    question: str
    allow_text: bool = False
    canned: dict[str, dict[str, Any]] = Field(default_factory=dict)
    required_any_tools: list[str]
    expected_state: str
    required_fragments: list[str]
    forbidden_fragments: list[str]


class GoldSet(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str
    prompt_version: str
    schema_version: str
    catalog_version: str
    synthetic_marker: str
    cases: list[GoldCase]


class Prediction(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    state: str
    error_code: str | None
    tools: list[str]
    body: str | None


class Report(BaseModel):
    model_config = ConfigDict(extra="forbid")

    gold_set_version: str
    prompt_version: str
    schema_version: str
    catalog_version: str
    model_name: str
    model_digest: str
    generated_at: datetime
    predictions: list[Prediction]


def _load_gold(path: Path = GOLD) -> GoldSet:
    return GoldSet.model_validate_json(path.read_text(encoding="utf-8"))


def _canned_output(case: GoldCase, tool_name: str, arguments: dict[str, Any]) -> ToolOutput:
    if tool_name == "describe_data":
        return execute_analysis_tool(
            AnalysisAccess(engine=None, allow_text=case.allow_text), tool_name, arguments
        )
    data = case.canned.get(
        tool_name,
        {"columns": [], "rows": [], "row_count": 0, "truncated": False, "views": []},
    )
    digest = hashlib.sha256(json.dumps(data, sort_keys=True).encode()).hexdigest()
    return ToolOutput(
        tool_name=tool_name,
        tool_version="synthetic-eval",
        ok=True,
        data=data,
        result_sha256=digest,
    )


def _evaluate(client: OllamaClient, gold: GoldSet, settings: Settings) -> Report:
    identity = client.identity()
    if identity is None:
        raise EvaluationError("chatbot_model_identity_missing")
    predictions: list[Prediction] = []
    for case in gold.cases:
        observed: list[str] = []

        def execute(
            tool_name: str,
            arguments: dict[str, Any],
            case: GoldCase = case,
            observed: list[str] = observed,
        ) -> ToolOutput:
            observed.append(tool_name)
            return _canned_output(case, tool_name, arguments)

        result = run(
            question=case.question,
            context=BoundedConversationContext(
                summary=None,
                turns=(ContextTurn(role=ChatRole.USER, body=case.question, sequence=1),),
                character_count=len(case.question),
            ),
            tools=tool_definitions(AnalysisAccess(engine=None, allow_text=case.allow_text)),
            execute_tool=execute,
            client=client,
            current_local_datetime=CURRENT_LOCAL_DATETIME,
            default_timezone=TIMEZONE,
            allow_text=case.allow_text,
            think=settings.chat_thinking,
            context_window=settings.chat_context_window or settings.ollama_context_window,
            max_output_tokens=settings.chat_max_output_tokens,
            read_timeout_s=settings.chat_read_timeout_s,
        )
        predictions.append(
            Prediction(
                id=case.id,
                state=result.state.value,
                error_code=result.error_code,
                tools=observed,
                body=result.body,
            )
        )
    return Report(
        gold_set_version=gold.version,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        catalog_version=CATALOG_VERSION,
        model_name=identity.name,
        model_digest=identity.digest,
        generated_at=datetime.now(UTC),
        predictions=predictions,
    )


def _verify(gold: GoldSet, report: Report) -> list[str]:
    if gold.synthetic_marker != SYNTHETIC_MARKER:
        raise EvaluationError("chatbot_gold_not_synthetic")
    if (
        gold.prompt_version != PROMPT_VERSION
        or report.prompt_version != PROMPT_VERSION
        or gold.schema_version != SCHEMA_VERSION
        or report.schema_version != SCHEMA_VERSION
    ):
        raise EvaluationError("chatbot_contract_version_mismatch")
    if gold.catalog_version != CATALOG_VERSION or report.catalog_version != CATALOG_VERSION:
        raise EvaluationError("chatbot_tool_catalog_version_mismatch")
    if not report.model_name or len(report.model_digest) < 32:
        raise EvaluationError("chatbot_model_identity_missing")
    by_id = {prediction.id: prediction for prediction in report.predictions}
    if len(by_id) != len(report.predictions) or set(by_id) != {case.id for case in gold.cases}:
        raise EvaluationError("chatbot_prediction_case_set_mismatch")
    failures: list[str] = []
    for case in gold.cases:
        observed = by_id[case.id]
        # Digit grouping ("6,421") is presentation; the answer validator treats it as 6421.
        body = _DIGIT_GROUPING.sub("", observed.body or "")
        if observed.state != case.expected_state:
            failures.append(
                f"{case.id}: state={observed.state}, expected={case.expected_state}, "
                f"error={observed.error_code}"
            )
        if case.required_any_tools and not set(case.required_any_tools) & set(observed.tools):
            failures.append(f"{case.id}: none of the required tools {case.required_any_tools}")
        for fragment in case.required_fragments:
            if fragment.lower() not in body.lower():
                failures.append(f"{case.id}: missing required fragment {fragment!r}")
        for fragment in case.forbidden_fragments:
            if fragment.lower() in body.lower():
                failures.append(f"{case.id}: included forbidden fragment {fragment!r}")
    return failures


def check(gold_path: Path = GOLD, baseline_path: Path = BASELINE) -> int:
    gold = _load_gold(gold_path)
    report = Report.model_validate_json(baseline_path.read_text(encoding="utf-8"))
    failures = _verify(gold, report)
    print(
        f"gold={gold.version} prompt={report.prompt_version} "
        f"model={report.model_name}@{report.model_digest[:12]}"
    )
    for failure in failures:
        print(f"FAIL: {failure}")
    return 1 if failures else 0


def record(
    gold_path: Path = GOLD,
    baseline_path: Path = BASELINE,
    model_name: str | None = None,
    settings: Settings | None = None,
) -> int:
    if model_name is not None and baseline_path.resolve() == BASELINE.resolve():
        raise EvaluationError("candidate_output_path_required")
    gold = _load_gold(gold_path)
    settings = settings or Settings()
    if model_name is not None:
        settings = settings.model_copy(update={"ollama_model": model_name})
    try:
        report = _evaluate(OllamaClient(settings), gold, settings)
        failures = _verify(gold, report)
    except EvaluationError as exc:
        print(f"chatbot evaluation failed: {exc}", file=sys.stderr)
        return 1
    for failure in failures:
        print(f"FAIL: {failure}")
    if failures:
        failed_ids = {failure.split(":", 1)[0] for failure in failures}
        for prediction in report.predictions:
            if prediction.id in failed_ids:
                # Synthetic fixtures only: showing the answer makes a failure diagnosable.
                excerpt = (prediction.body or "")[:600]
                print(
                    f"DETAIL {prediction.id}: state={prediction.state} "
                    f"error={prediction.error_code} tools={prediction.tools} body={excerpt!r}"
                )
        return 1
    baseline_path.parent.mkdir(parents=True, exist_ok=True)
    baseline_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    print(f"recorded {len(report.predictions)} chatbot cases for {report.model_name}")
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
    except (OSError, ValueError, EvaluationError) as exc:
        print(f"chatbot evaluation failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
