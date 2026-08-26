"""Verify a local Ollama candidate without changing HealthCurve's selected model."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from healthcurve.ai.model_qualification import (
    QWEN38_CANDIDATE_MODEL,
    QWEN38_MIN_OLLAMA_VERSION,
    CandidatePreflightError,
    run_candidate_preflight,
)
from healthcurve.config import Settings


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--model", default=QWEN38_CANDIDATE_MODEL)
    parser.add_argument("--minimum-version", default=QWEN38_MIN_OLLAMA_VERSION)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()

    try:
        settings = Settings(
            ollama_base_url=args.base_url,
            ollama_model=args.model,
            ollama_thinking=False,
        )
        report = run_candidate_preflight(
            settings,
            model_name=args.model,
            minimum_ollama_version=args.minimum_version,
        )
    except (CandidatePreflightError, ValueError) as exc:
        print(f"candidate preflight failed: {exc}", file=sys.stderr)
        return 1

    rendered = json.dumps(report.model_dump(mode="json"), indent=2, sort_keys=True) + "\n"
    if args.output is not None:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered, encoding="utf-8")
    print(
        f"candidate ready: {report.model_name}@{report.model_digest[:12]} "
        f"ollama={report.ollama_version} latency_ms={report.latency_ms}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
