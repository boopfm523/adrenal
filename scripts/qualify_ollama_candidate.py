"""Run all-synthetic HealthCurve gates against a non-default Ollama candidate."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from healthcurve.ai.model_qualification import (
    QWEN38_CANDIDATE_MODEL,
    CandidatePreflightError,
    CandidateQualificationReport,
    CandidateSuiteResult,
    run_candidate_preflight,
)
from healthcurve.config import Settings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT = ROOT / "evals" / "candidates" / "qwen3.8-27b-q8_0"


def _timed_suite(
    name: str,
    path: Path,
    command: list[str],
    environment: dict[str, str],
) -> CandidateSuiteResult:
    started = time.monotonic()
    completed = subprocess.Popen(  # noqa: S603
        command,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    if completed.stdout is None:  # pragma: no cover - guaranteed by PIPE
        raise RuntimeError("candidate_suite_output_unavailable")
    output_lines: list[str] = []
    for line in completed.stdout:
        print(line, end="", flush=True)
        output_lines.append(line)
    return_code = completed.wait()
    try:
        report_path = str(path.relative_to(ROOT))
    except ValueError:
        report_path = str(path)
    return CandidateSuiteResult(
        name=name,
        passed=return_code == 0,
        duration_ms=int((time.monotonic() - started) * 1000),
        report_path=report_path,
        failure_detail=(
            None
            if return_code == 0
            else "".join(output_lines)[-4_000:].strip() or f"exit_code={return_code}"
        ),
    )


def qualify(*, base_url: str, model_name: str, output_dir: Path) -> int:
    settings = Settings(
        ollama_base_url=base_url,
        ollama_model=model_name,
        ollama_thinking=False,
    )
    try:
        preflight = run_candidate_preflight(settings, model_name=model_name)
    except CandidatePreflightError as exc:
        print(f"candidate qualification failed during preflight: {exc}", file=sys.stderr)
        return 1

    output_dir.mkdir(parents=True, exist_ok=True)
    extraction_path = output_dir / "extraction.json"
    chatbot_path = output_dir / "chatbot.json"
    analysis_path = output_dir / "analysis.json"
    environment = os.environ.copy()
    environment.update(
        {
            "HC_OLLAMA_BASE_URL": base_url,
            "HC_OLLAMA_MODEL": model_name,
            "HC_OLLAMA_THINKING": "false",
        }
    )

    def command(script_name: str, report_path: Path) -> list[str]:
        return [
            sys.executable,
            str(ROOT / "scripts" / script_name),
            "--record",
            "--model",
            model_name,
            "--baseline",
            str(report_path),
        ]

    suites = [
        _timed_suite(
            "extraction",
            extraction_path,
            command("evaluate_extraction.py", extraction_path),
            environment,
        ),
        _timed_suite(
            "chatbot",
            chatbot_path,
            command("evaluate_chatbot.py", chatbot_path),
            environment,
        ),
        _timed_suite(
            "analysis",
            analysis_path,
            command("evaluate_analysis.py", analysis_path),
            environment,
        ),
    ]
    report = CandidateQualificationReport(
        generated_at=datetime.now(UTC),
        candidate=preflight,
        suites=suites,
        passed=all(suite.passed for suite in suites),
    )
    qualification_path = output_dir / "qualification.json"
    qualification_path.write_text(
        json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    for suite in suites:
        state = "PASS" if suite.passed else "FAIL"
        print(f"{state}: {suite.name} duration_ms={suite.duration_ms}")
    try:
        qualification_display = qualification_path.relative_to(ROOT)
    except ValueError:
        qualification_display = qualification_path
    print(f"qualification={qualification_display}")
    return 0 if report.passed else 1


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default=QWEN38_CANDIDATE_MODEL)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    try:
        return qualify(
            base_url=args.base_url,
            model_name=args.model,
            output_dir=args.output_dir,
        )
    except (OSError, ValueError) as exc:
        print(f"candidate qualification failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
