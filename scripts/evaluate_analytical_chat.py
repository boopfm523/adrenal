"""Verify or record the analytical chat evaluation on synthetic data (ADR-0036).

Check mode (the default, used by ``make eval``) re-grades the recorded reports against
the gold set and ground truth recomputed from the deterministic synthetic fixture. It
needs neither a database nor a model.

``--record`` starts a disposable PostgreSQL with the real init scripts, migrates and
seeds the synthetic fixture, and asks the configured local Ollama model every question
through the real chat orchestration and analysis tools, with thinking on and off. It
never touches the HealthCurve database and never sends data to a cloud service.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

from healthcurve.analysis.catalog import CATALOG_VERSION
from healthcurve.analysis.tools import (
    AnalysisAccess,
    ToolOutput,
    execute_analysis_tool,
    tool_definitions,
)
from healthcurve.analytical_evaluation import (
    CURRENT_LOCAL_DATETIME,
    FIXTURE_VERSION,
    SYNTHETIC_MARKER,
    TIMEZONE,
    AnalyticalGold,
    AnalyticalPrediction,
    AnalyticalReport,
    compute_truth,
    generate_dataset,
    grade_report,
)
from healthcurve.chat.models import ChatRole
from healthcurve.chat.orchestration import PROMPT_VERSION, SCHEMA_VERSION, run
from healthcurve.chat.service import BoundedConversationContext, ContextTurn

ROOT = Path(__file__).resolve().parents[1]
GOLD = ROOT / "evals" / "analytical_chat" / "gold-v2.json"
REPORTS = {
    True: ROOT / "evals" / "analytical_chat" / "report-thinking-on.json",
    False: ROOT / "evals" / "analytical_chat" / "report-thinking-off.json",
}


def _load_gold(path: Path = GOLD) -> AnalyticalGold:
    return AnalyticalGold.model_validate_json(path.read_text(encoding="utf-8"))


def _contract_problems(gold: AnalyticalGold, report: AnalyticalReport | None = None) -> list[str]:
    problems: list[str] = []
    if gold.synthetic_marker != SYNTHETIC_MARKER:
        problems.append("gold set is not marked synthetic")
    expected = {
        "fixture_version": FIXTURE_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_version": SCHEMA_VERSION,
        "catalog_version": CATALOG_VERSION,
    }
    for field, current in expected.items():
        if getattr(gold, field) != current:
            problems.append(f"gold {field}={getattr(gold, field)} but current is {current}")
        if report is not None and getattr(report, field) != current:
            problems.append(f"report {field}={getattr(report, field)} but current is {current}")
    if report is not None:
        if report.gold_version != gold.version:
            problems.append(f"report gold_version={report.gold_version}")
        if not report.model_name or len(report.model_digest) < 32:
            problems.append("report model identity missing")
    return problems


def check(gold_path: Path = GOLD) -> int:
    gold = _load_gold(gold_path)
    truth = compute_truth(generate_dataset())
    failed = False
    for thinking, path in REPORTS.items():
        label = "thinking-on" if thinking else "thinking-off"
        if not path.exists():
            print(f"FAIL: {label}: report missing ({path.name})")
            failed = True
            continue
        report = AnalyticalReport.model_validate_json(path.read_text(encoding="utf-8"))
        problems = _contract_problems(gold, report)
        if report.thinking != thinking:
            problems.append(f"report thinking={report.thinking}")
        summary = grade_report(gold, report, truth)
        print(
            f"{label}: model={report.model_name}@{report.model_digest[:12]} "
            f"pass_rate={summary.pass_rate:.2f} owner_examples_passed="
            f"{summary.owner_examples_passed} median_latency_ms={summary.median_latency_ms}"
        )
        predictions = {prediction.id: prediction for prediction in report.predictions}
        for case_id, reasons in summary.failures.items():
            print(f"  case {case_id}: {'; '.join(reasons)}")
            prediction = predictions.get(case_id)
            for rejection in [] if prediction is None else prediction.rejections:
                print(f"    rejected: {rejection}")
        # The configured default (thinking on) is the release gate; thinking off is
        # recorded for comparison only.
        if thinking and (
            not summary.owner_examples_passed or summary.pass_rate < gold.minimum_pass_rate
        ):
            problems.append("thinking-on report is below the release threshold")
        for problem in problems:
            print(f"FAIL: {label}: {problem}")
        failed = failed or bool(problems)
    return 1 if failed else 0


def _record_mode(
    *,
    gold: AnalyticalGold,
    thinking: bool,
    client: Any,
    settings: Any,
    access_for: Any,
    identity: Any,
) -> AnalyticalReport:
    predictions: list[AnalyticalPrediction] = []
    for case in gold.cases:
        access: AnalysisAccess = access_for(case.allow_text)
        observed: list[str] = []
        # Synthetic fixture only: keep validator feedback so rejected answers can be diagnosed.
        rejections: list[str] = []

        def execute(
            name: str,
            arguments: dict[str, Any],
            access: AnalysisAccess = access,
            observed: list[str] = observed,
        ) -> ToolOutput:
            observed.append(name)
            return execute_analysis_tool(access, name, arguments)

        started = time.monotonic()
        result = run(
            question=case.question,
            context=BoundedConversationContext(
                summary=None,
                turns=(ContextTurn(role=ChatRole.USER, body=case.question, sequence=1),),
                character_count=len(case.question),
            ),
            tools=tool_definitions(access),
            execute_tool=execute,
            client=client,
            current_local_datetime=CURRENT_LOCAL_DATETIME,
            default_timezone=TIMEZONE,
            allow_text=case.allow_text,
            think=thinking,
            context_window=settings.chat_context_window or settings.ollama_context_window,
            max_output_tokens=settings.chat_max_output_tokens,
            read_timeout_s=settings.chat_read_timeout_s,
            observe_rejection=lambda code, feedback, rejections=rejections: rejections.append(
                f"{code}: {feedback}"
            ),
        )
        latency_ms = int((time.monotonic() - started) * 1000)
        print(f"  {'on ' if thinking else 'off'} {case.id}: {result.state.value} {latency_ms} ms")
        predictions.append(
            AnalyticalPrediction(
                id=case.id,
                state=result.state.value,
                error_code=result.error_code,
                tools=observed,
                body=result.body,
                latency_ms=latency_ms,
                rejections=rejections,
            )
        )
    return AnalyticalReport(
        gold_version=gold.version,
        fixture_version=FIXTURE_VERSION,
        prompt_version=PROMPT_VERSION,
        schema_version=SCHEMA_VERSION,
        catalog_version=CATALOG_VERSION,
        thinking=thinking,
        model_name=identity.name,
        model_digest=identity.digest,
        generated_at=datetime.now(UTC),
        predictions=predictions,
    )


def record(modes: list[bool], gold_path: Path = GOLD) -> int:
    # Heavy imports only for the live run so check mode stays dependency-light.
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine
    from sqlalchemy.orm import Session, sessionmaker
    from testcontainers.community.postgres import PostgresContainer

    import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
    from healthcurve.ai.ollama import OllamaClient
    from healthcurve.analytical_evaluation import seed_database
    from healthcurve.config import Settings, get_settings

    gold = _load_gold(gold_path)
    problems = _contract_problems(gold)
    if problems:
        for problem in problems:
            print(f"FAIL: {problem}")
        return 1
    settings = Settings()
    client = OllamaClient(settings)
    identity = client.identity()
    if identity is None:
        print("FAIL: local model identity unavailable (check HC_OLLAMA_BASE_URL)", file=sys.stderr)
        return 1

    passwords = {
        role: secrets.token_urlsafe(24) for role in ("healthcurve", "ai", "analyst", "text")
    }
    container = (
        PostgresContainer(
            "postgres:16-alpine",
            username="healthcurve",
            password=passwords["healthcurve"],
            dbname="healthcurve",
            driver="psycopg",
        )
        .with_env("POSTGRES_AI_PASSWORD", passwords["ai"])
        .with_env("POSTGRES_ANALYST_PASSWORD", passwords["analyst"])
        .with_env("POSTGRES_ANALYST_TEXT_PASSWORD", passwords["text"])
        .with_volume_mapping(
            str(ROOT / "deploy" / "postgres-init"), "/docker-entrypoint-initdb.d", "ro"
        )
    )
    truth = compute_truth(generate_dataset())
    exit_code = 0
    with container as running:
        owner_url = running.get_connection_url()
        config = Config(str(ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(ROOT / "migrations"))
        with mock.patch.dict(os.environ, {"HC_DATABASE_URL": owner_url}):
            get_settings.cache_clear()
            command.upgrade(config, "head")
        get_settings.cache_clear()

        def role_url(role: str, key: str) -> str:
            return owner_url.replace(
                f"healthcurve:{passwords['healthcurve']}@", f"{role}:{passwords[key]}@"
            )

        owner_engine = create_engine(owner_url)
        with Session(owner_engine) as session, session.begin():
            owner_id = seed_database(session, generate_dataset())
        analyst = create_engine(role_url("healthcurve_analyst", "analyst"))
        analyst_text = create_engine(role_url("healthcurve_analyst_text", "text"))
        ai_factory = sessionmaker(create_engine(role_url("healthcurve_ai", "ai")))

        def access_for(allow_text: bool) -> AnalysisAccess:
            return AnalysisAccess(
                engine=analyst,
                text_engine=analyst_text,
                allow_text=allow_text,
                owner_id=owner_id,
                timezone=TIMEZONE,
                model_session_factory=ai_factory,
            )

        for thinking in modes:
            print(f"recording thinking={'on' if thinking else 'off'} with {identity.name}")
            report = _record_mode(
                gold=gold,
                thinking=thinking,
                client=client,
                settings=settings,
                access_for=access_for,
                identity=identity,
            )
            REPORTS[thinking].parent.mkdir(parents=True, exist_ok=True)
            REPORTS[thinking].write_text(
                json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
            summary = grade_report(gold, report, truth)
            print(
                f"thinking={'on' if thinking else 'off'} pass_rate={summary.pass_rate:.2f} "
                f"owner_examples_passed={summary.owner_examples_passed} "
                f"median_latency_ms={summary.median_latency_ms}"
            )
            for case_id, reasons in summary.failures.items():
                print(f"  case {case_id}: {'; '.join(reasons)}")
            if thinking and (
                not summary.owner_examples_passed or summary.pass_rate < gold.minimum_pass_rate
            ):
                exit_code = 1
    return exit_code


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--record", action="store_true")
    parser.add_argument(
        "--thinking", choices=("on", "off", "both"), default="both", help="Modes to record."
    )
    parser.add_argument("--gold", type=Path, default=GOLD)
    args = parser.parse_args()
    if not args.record:
        return check(args.gold)
    modes = {"on": [True], "off": [False], "both": [True, False]}[args.thinking]
    return record(modes, args.gold)


if __name__ == "__main__":
    raise SystemExit(main())
