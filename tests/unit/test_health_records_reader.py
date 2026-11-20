"""Reading the external health-records dataset safely and to contract (ADR-0040).

All data is the synthetic fixture in ``tests/fixtures/health_records_dataset.py``.
"""

from __future__ import annotations

import hashlib
import os
import stat
from collections.abc import Callable, Iterator
from pathlib import Path
from unittest import mock

import pytest

from healthcurve.integrations.health_records import reader
from healthcurve.integrations.health_records.models import DatasetTable
from healthcurve.integrations.health_records.reader import (
    ContractViolationError,
    DatasetNotFoundError,
    SnapshotChangedError,
    UnsupportedSchemaError,
    read_snapshot,
)
from tests.fixtures.health_records_dataset import (
    SCHEMA,
    default_meta,
    default_tables,
    write_dataset,
)


def _sequence(values: Iterator[str]) -> Callable[[object], str]:
    """Stand-in for ``_generated_at`` returning successive values per call."""

    def generated_at(_connection: object) -> str:
        return next(values)

    return generated_at


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def test_reads_every_table_and_dataset_provenance(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    snapshot = read_snapshot(tmp_path)
    assert snapshot.schema_version == 2
    assert snapshot.generated_at == "2025-06-01T12:00:00"
    assert snapshot.source_exported_at == {
        "Johns Hopkins MyChart": "2025-06-01",
        "NIH Clinical Center": "2025-05-30",
    }
    assert snapshot.count(DatasetTable.LAB_RESULTS) == 7
    assert snapshot.count(DatasetTable.MEDICATIONS) == 1
    assert set(snapshot.rows) == set(DatasetTable)
    assert len(snapshot.sha256) == 64


def test_never_writes_to_the_dataset(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    database = tmp_path / "data" / "health.db"
    before = _digest(database)
    database.chmod(stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH)
    try:
        read_snapshot(tmp_path)
    finally:
        database.chmod(stat.S_IRUSR | stat.S_IWUSR)
    assert _digest(database) == before
    assert sorted(path.name for path in database.parent.iterdir()) == ["health.db"]


def test_extra_undocumented_columns_are_kept_not_required(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    rows = read_snapshot(tmp_path).rows[DatasetTable.CONDITIONS]
    assert "icd_code" in rows[0]


@pytest.mark.parametrize("version", ["1", "3", "two", ""])
def test_unknown_schema_version_is_refused(tmp_path: Path, version: str) -> None:
    meta = default_meta()
    meta["schema_version"] = version
    write_dataset(tmp_path, meta=meta)
    with pytest.raises(UnsupportedSchemaError):
        read_snapshot(tmp_path)


def test_missing_schema_version_is_refused(tmp_path: Path) -> None:
    meta = default_meta()
    del meta["schema_version"]
    write_dataset(tmp_path, meta=meta)
    with pytest.raises(UnsupportedSchemaError):
        read_snapshot(tmp_path)


def test_missing_documented_column_is_refused(tmp_path: Path) -> None:
    schema = SCHEMA.replace("reading_provider TEXT, ", "")
    tables = default_tables()
    for row in tables["imaging_studies"]:
        del row["reading_provider"]
    write_dataset(tmp_path, tables=tables, schema=schema)
    with pytest.raises(ContractViolationError, match="reading_provider"):
        read_snapshot(tmp_path)


def test_missing_table_is_refused(tmp_path: Path) -> None:
    schema = SCHEMA.replace(
        "CREATE TABLE surgical_history (\n  source TEXT, organization TEXT, name TEXT, "
        "date_text TEXT, record_id TEXT);",
        "",
    )
    tables = default_tables()
    del tables["surgical_history"]
    write_dataset(tmp_path, tables=tables, schema=schema)
    with pytest.raises(ContractViolationError, match="surgical_history"):
        read_snapshot(tmp_path)


def test_duplicate_record_id_is_refused(tmp_path: Path) -> None:
    tables = default_tables()
    tables["lab_results"][1]["record_id"] = tables["lab_results"][0]["record_id"]
    write_dataset(tmp_path, tables=tables)
    with pytest.raises(ContractViolationError, match="repeats"):
        read_snapshot(tmp_path)


@pytest.mark.parametrize("column", ["record_id", "organization", "source"])
def test_row_without_identity_or_provenance_is_refused(tmp_path: Path, column: str) -> None:
    tables = default_tables()
    tables["documents"][0][column] = None
    write_dataset(tmp_path, tables=tables)
    with pytest.raises(ContractViolationError):
        read_snapshot(tmp_path)


def test_missing_database_is_reported(tmp_path: Path) -> None:
    with pytest.raises(DatasetNotFoundError):
        read_snapshot(tmp_path)


def test_database_symlinked_outside_the_root_is_refused(tmp_path: Path) -> None:
    elsewhere = tmp_path / "elsewhere"
    write_dataset(elsewhere)
    root = tmp_path / "root"
    (root / "data").mkdir(parents=True)
    os.symlink(elsewhere / "data" / "health.db", root / "data" / "health.db")
    with pytest.raises(DatasetNotFoundError, match="outside"):
        read_snapshot(root)


def test_rebuild_during_copy_is_retried_then_refused(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    values = iter(["a", "b", "c", "d"])
    with (
        mock.patch.object(reader, "_generated_at", side_effect=_sequence(values)),
        pytest.raises(SnapshotChangedError),
    ):
        read_snapshot(tmp_path, attempts=2)


def test_rebuild_during_first_copy_succeeds_on_retry(tmp_path: Path) -> None:
    write_dataset(tmp_path)
    values = iter(["a", "b", "c", "c"])
    with mock.patch.object(reader, "_generated_at", side_effect=_sequence(values)):
        snapshot = read_snapshot(tmp_path, attempts=2)
    assert snapshot.count(DatasetTable.DOCUMENTS) == 2
