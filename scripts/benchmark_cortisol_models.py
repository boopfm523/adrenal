#!/usr/bin/env python3
"""Run the in-memory dense selected-day cortisol-model benchmark."""

from __future__ import annotations

import argparse

from healthcurve.cortisol_benchmark import result_json, run_benchmark


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--runs", type=int, default=7)
    parser.add_argument(
        "--check",
        action="store_true",
        help="return a failing exit status when a latency or memory budget is exceeded",
    )
    args = parser.parse_args()
    result = run_benchmark(runs=args.runs)
    print(result_json(result), end="")
    return 1 if args.check and not result["all_within_budget"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
