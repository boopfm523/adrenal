from __future__ import annotations

from pathlib import Path

import pytest

from healthcurve.operations.backup import BackupError
from healthcurve.operations.restore_drill import assert_restore_canary


def test_restore_canary_opens_and_matches(tmp_path: Path) -> None:
    path = tmp_path / "canary.json"
    path.write_text(
        '{"format_version":1,"kind":"healthcurve_restore_canary","synthetic":true}\n',
        encoding="utf-8",
    )
    assert assert_restore_canary(path) is None


def test_malformed_restore_canary_has_stable_reason(tmp_path: Path) -> None:
    path = tmp_path / "canary.json"
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(BackupError, match=r"^restore_artifact_canary_invalid$"):
        assert_restore_canary(path)


def test_changed_restore_canary_has_stable_reason(tmp_path: Path) -> None:
    path = tmp_path / "canary.json"
    path.write_text('{"format_version":2}\n', encoding="utf-8")
    with pytest.raises(BackupError, match=r"^restore_artifact_canary_mismatch$"):
        assert_restore_canary(path)
