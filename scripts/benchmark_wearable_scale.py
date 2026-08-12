#!/usr/bin/env python3
"""Run HealthCurve's rollback-only multi-year wearable benchmark."""

from __future__ import annotations

import argparse
import os
from pathlib import Path

from healthcurve.db import build_engine
from healthcurve.wearable_benchmark import result_json, run_benchmark

CONFIRMATION = "ROLLBACK-SYNTHETIC-WEARABLE-BENCHMARK"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("HC_DATABASE_URL"))
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--output", type=Path)
    parser.add_argument(
        "--confirm",
        help=f"Required literal safety acknowledgement: {CONFIRMATION}",
    )
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        parser.error(f"--confirm must equal {CONFIRMATION}")
    if not args.database_url:
        parser.error("--database-url or HC_DATABASE_URL is required")

    engine = build_engine(args.database_url)
    try:
        rendered = result_json(run_benchmark(engine, years=args.years, runs=args.runs))
    finally:
        engine.dispose()
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
