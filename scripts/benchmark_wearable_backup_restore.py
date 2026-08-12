#!/usr/bin/env python3
"""Measure a synthetic multi-year pg_dump and isolated pg_restore."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from healthcurve.wearable_retention_benchmark import run

CONFIRMATION = "CREATE-DISPOSABLE-SYNTHETIC-BACKUP-RESTORE"
REPO_ROOT = Path(__file__).resolve().parents[1]


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--years", type=int, default=5)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--confirm")
    args = parser.parse_args()
    if args.confirm != CONFIRMATION:
        parser.error(f"--confirm must equal {CONFIRMATION}")
    rendered = (
        json.dumps(run(years=args.years, repo_root=REPO_ROOT), indent=2, sort_keys=True) + "\n"
    )
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    else:
        print(rendered, end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
