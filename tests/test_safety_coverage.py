"""CI gate tying docs/safety-spec.md to the test suite.

Prose rules rot. This turns ``docs/safety-rules.yaml`` into something CI enforces:

1. Every ``@pytest.mark.safety("SAFE-nn")`` names a rule that actually exists, so a
   typo or a deleted rule fails the build instead of silently covering nothing.
2. Every rule marked ``status: enforced`` has at least one test. Once a rule is
   enforced it can never quietly lose its coverage.
3. Rules not yet implemented are ``pending`` and are *reported*, so the gap is
   visible rather than forgotten. Flipping a rule to ``enforced`` is how a phase
   claims it.

A rule may never move from ``enforced`` back to ``pending`` without an ADR.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import pytest
import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
RULES_FILE = REPO_ROOT / "docs" / "safety-rules.yaml"
SPEC_FILE = REPO_ROOT / "docs" / "safety-spec.md"
TESTS_DIR = REPO_ROOT / "tests"

_MARKER_RE = re.compile(r"""@pytest\.mark\.safety\(\s*["'](SAFE-\d+)["']""")


def _load_rules() -> list[dict[str, Any]]:
    data = yaml.safe_load(RULES_FILE.read_text(encoding="utf-8"))
    rules: list[dict[str, Any]] = data["rules"]
    return rules


def _marked_rule_ids() -> set[str]:
    marked: set[str] = set()
    for path in TESTS_DIR.rglob("*.py"):
        marked.update(_MARKER_RE.findall(path.read_text(encoding="utf-8")))
    return marked


def test_index_matches_spec_document() -> None:
    """The machine-readable index and the prose spec describe the same rule set."""
    spec_ids = re.findall(r"^### (SAFE-\d+)", SPEC_FILE.read_text(encoding="utf-8"), re.MULTILINE)
    index_ids = [rule["id"] for rule in _load_rules()]

    assert len(spec_ids) == len(set(spec_ids)), "duplicate rule heading in safety-spec.md"
    assert len(index_ids) == len(set(index_ids)), "duplicate rule id in safety-rules.yaml"
    assert set(spec_ids) == set(index_ids), (
        f"safety-spec.md and safety-rules.yaml disagree: "
        f"only in spec={sorted(set(spec_ids) - set(index_ids))}, "
        f"only in index={sorted(set(index_ids) - set(spec_ids))}"
    )


def test_every_safety_marker_names_a_real_rule() -> None:
    """A test cannot claim to enforce a rule that does not exist."""
    known = {rule["id"] for rule in _load_rules()}
    unknown = _marked_rule_ids() - known
    assert not unknown, (
        f"tests reference unknown safety rule ids {sorted(unknown)}; "
        f"add them to docs/safety-rules.yaml or fix the marker"
    )


def test_enforced_rules_have_coverage() -> None:
    """Once a rule is enforced, it keeps its test. This is the regression gate."""
    marked = _marked_rule_ids()
    missing = [
        rule["id"]
        for rule in _load_rules()
        if rule.get("test_required")
        and rule.get("status") == "enforced"
        and rule["id"] not in marked
    ]
    assert not missing, (
        f"rules marked status: enforced have no test: {sorted(missing)}. "
        f"Add a @pytest.mark.safety test, or the rule is not actually enforced."
    )


def test_report_pending_safety_rules(record_property: Any) -> None:
    """Not an assertion -- makes the remaining safety surface visible in CI output."""
    marked = _marked_rule_ids()
    pending = sorted(
        rule["id"]
        for rule in _load_rules()
        if rule.get("test_required") and rule.get("status") != "enforced"
    )
    covered = sorted(rule["id"] for rule in _load_rules() if rule.get("status") == "enforced")
    record_property("safety_rules_enforced", ",".join(covered))
    record_property("safety_rules_pending", ",".join(pending))

    # Any rule already carrying a test but still marked pending should be promoted.
    promotable = sorted(set(pending) & marked)
    if promotable:
        pytest.fail(
            f"these rules have tests but are still status: pending in "
            f"docs/safety-rules.yaml -- promote them to 'enforced': {promotable}"
        )
