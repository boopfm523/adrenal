from __future__ import annotations

import json
from pathlib import Path

from scripts.check_secret_baseline import check_baseline


def _baseline(path: Path, finding: dict[str, object]) -> Path:
    path.write_text(json.dumps({"results": {"synthetic.py": [finding]}}), encoding="utf-8")
    return path


def test_reviewed_false_positive_passes(tmp_path: Path) -> None:
    path = _baseline(tmp_path / "baseline.json", {"line_number": 1, "is_secret": False})
    assert check_baseline(path) == []


def test_unreviewed_finding_fails(tmp_path: Path) -> None:
    path = _baseline(tmp_path / "baseline.json", {"line_number": 2})
    assert check_baseline(path) == ["synthetic.py:2: finding has not been reviewed"]


def test_confirmed_secret_fails(tmp_path: Path) -> None:
    path = _baseline(tmp_path / "baseline.json", {"line_number": 3, "is_secret": True})
    assert check_baseline(path) == ["synthetic.py:3: confirmed secret must be removed"]
