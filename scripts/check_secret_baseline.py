"""Fail CI when detect-secrets findings are unreviewed or confirmed real."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

BASELINE = Path(__file__).resolve().parents[1] / ".secrets.baseline"


def check_baseline(path: Path = BASELINE) -> list[str]:
    data: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    failures: list[str] = []
    results = data.get("results", {})
    if not isinstance(results, dict):
        return ["baseline results are malformed"]
    for filename, findings in sorted(results.items()):
        if not isinstance(findings, list):
            failures.append(f"{filename}: findings are malformed")
            continue
        for finding in findings:
            line = finding.get("line_number", "unknown")
            decision = finding.get("is_secret")
            if decision is None:
                failures.append(f"{filename}:{line}: finding has not been reviewed")
            elif decision is True:
                failures.append(f"{filename}:{line}: confirmed secret must be removed")
            elif decision is not False:
                failures.append(f"{filename}:{line}: invalid review decision")
    return failures


def main() -> int:
    failures = check_baseline()
    for failure in failures:
        print(f"secret baseline failure: {failure}", file=sys.stderr)
    if failures:
        return 1
    print("secret baseline: every finding reviewed; no confirmed secrets")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
