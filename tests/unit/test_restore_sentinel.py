from __future__ import annotations

from contextlib import nullcontext
from typing import Any

import pytest

from healthcurve.operations.backup import BackupError
from healthcurve.operations.restore_drill import assert_restore_sentinel
from healthcurve.operations.restore_sentinel import expected_restore_sentinel


class _Result:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    def one_or_none(self) -> tuple[object, ...] | None:
        return self.row


class _Connection:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    def execute(self, *_args: Any, **_kwargs: Any) -> _Result:
        return _Result(self.row)


class _Engine:
    def __init__(self, row: tuple[object, ...] | None) -> None:
        self.row = row

    def connect(self) -> nullcontext[_Connection]:
        return nullcontext(_Connection(self.row))


def test_exact_restore_sentinel_passes_without_returning_values() -> None:
    assert assert_restore_sentinel(_Engine(expected_restore_sentinel())) is None  # type: ignore[arg-type]


def test_missing_restore_sentinel_has_stable_reason_code() -> None:
    with pytest.raises(BackupError, match=r"^restore_sentinel_missing$"):
        assert_restore_sentinel(_Engine(None))  # type: ignore[arg-type]


def test_altered_restore_sentinel_has_stable_reason_code() -> None:
    altered = list(expected_restore_sentinel())
    altered[-2] = "Etc/UTC"
    with pytest.raises(BackupError, match=r"^restore_sentinel_mismatch$"):
        assert_restore_sentinel(_Engine(tuple(altered)))  # type: ignore[arg-type]
