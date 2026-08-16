from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
WORKFLOWS = ROOT / ".github" / "workflows"
EXTERNAL_ACTION = re.compile(r"^\s*-?\s*uses:\s+(?P<action>[^\s#]+)\s*(?P<comment>#.*)?$")
IMMUTABLE_ACTION = re.compile(r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+@[0-9a-f]{40}$")


def test_external_github_actions_are_pinned_to_full_commit_shas() -> None:
    found: list[tuple[Path, int, str]] = []
    failures: list[str] = []

    for workflow in sorted(WORKFLOWS.glob("*.y*ml")):
        for line_number, line in enumerate(
            workflow.read_text(encoding="utf-8").splitlines(), start=1
        ):
            match = EXTERNAL_ACTION.match(line)
            if match is None:
                continue
            action = match.group("action")
            if action.startswith("./") or action.startswith("docker://"):
                continue
            found.append((workflow, line_number, action))
            if not IMMUTABLE_ACTION.fullmatch(action):
                failures.append(f"{workflow.relative_to(ROOT)}:{line_number}: {action}")
            if not (match.group("comment") or "").strip().startswith("# v"):
                failures.append(
                    f"{workflow.relative_to(ROOT)}:{line_number}: missing version comment"
                )

    assert found, "expected at least one external GitHub Action"
    assert not failures, "external Actions must use a full SHA and '# vN' comment:\n" + "\n".join(
        failures
    )
