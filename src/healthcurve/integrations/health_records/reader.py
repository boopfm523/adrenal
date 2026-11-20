"""Read one consistent, contract-checked snapshot of the external dataset (ADR-0040).

The collector rewrites ``health.db`` in place, so the database is never read directly.
It is opened read-only (``mode=ro``, deliberately not ``immutable=1``), copied with
SQLite's online backup API into a private temporary file, and every row is read from
that copy. ``dataset_meta.generated_at`` is compared before and after the copy; a
rebuild that lands mid-copy is retried and then refused.

Nothing in this module writes to the dataset or to HealthCurve.
"""

from __future__ import annotations

import hashlib
import sqlite3
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Final
from urllib.parse import quote

from healthcurve.integrations.health_records.models import DatasetTable

SUPPORTED_SCHEMA_VERSION: Final = 2
DATABASE_RELATIVE_PATH: Final = Path("data") / "health.db"
EXPORTED_AT_PREFIX: Final = "exported_at:"

#: Columns every table must carry (DATASET.md "Provenance" and "Record identity").
_PROVENANCE: Final = frozenset({"record_id", "source", "organization"})

#: Columns DATASET.md v2 documents per table. Extra columns are kept, not interpreted.
REQUIRED_COLUMNS: Final[Mapping[DatasetTable, frozenset[str]]] = {
    DatasetTable.LAB_RESULTS: frozenset(
        {
            "date",
            "datetime",
            "panel",
            "test",
            "test_code",
            "test_normalized",
            "value_num",
            "value_text",
            "units",
            "ref_range",
            "ref_low",
            "ref_high",
            "flag",
            "ordered_by",
            "performing_lab",
            "specimen",
            "file_path",
        }
    ),
    DatasetTable.IMAGING_STUDIES: frozenset(
        {
            "date",
            "exam_name",
            "exam_normalized",
            "modality",
            "accession",
            "ordered_by",
            "reading_provider",
            "impression",
            "report_text",
            "report_file",
            "pdf_file",
            "is_outside_import",
        }
    ),
    DatasetTable.DOCUMENTS: frozenset(
        {
            "category",
            "kind",
            "title",
            "date",
            "provider",
            "file_path",
            "file_type",
            "original_id",
            "text",
        }
    ),
    DatasetTable.ENCOUNTERS: frozenset(
        {
            "date",
            "time",
            "visit_type",
            "department",
            "specialty",
            "provider",
            "status",
            "note_count",
            "note_files",
            "avs_file",
        }
    ),
    DatasetTable.MEDICATIONS: frozenset(
        {"name", "sig", "start_date", "date_noted", "prescriber", "patient_reported"}
    ),
    DatasetTable.CONDITIONS: frozenset({"name", "status", "onset_date"}),
    DatasetTable.IMMUNIZATIONS: frozenset({"name", "date", "status"}),
    DatasetTable.ALLERGIES: frozenset({"name", "status", "reactions"}),
    # DATASET.md does not list vitals columns (reported as a contract gap); these are
    # the ones the importer relies on.
    DatasetTable.VITALS: frozenset({"name", "date"}),
    DatasetTable.SURGICAL_HISTORY: frozenset({"name", "date_text"}),
}


class DatasetError(RuntimeError):
    """The dataset cannot be imported. Nothing has been written."""


class DatasetNotFoundError(DatasetError):
    pass


class UnsupportedSchemaError(DatasetError):
    pass


class ContractViolationError(DatasetError):
    pass


class SnapshotChangedError(DatasetError):
    """The collector rebuilt the dataset while it was being copied."""


@dataclass(frozen=True)
class DatasetSnapshot:
    schema_version: int
    generated_at: str
    source_exported_at: dict[str, str]
    sha256: str
    rows: dict[DatasetTable, list[dict[str, Any]]] = field(default_factory=dict)

    def count(self, table: DatasetTable) -> int:
        return len(self.rows.get(table, []))


def database_path(dataset_root: Path) -> Path:
    root = dataset_root.resolve(strict=False)
    path = (root / DATABASE_RELATIVE_PATH).resolve(strict=False)
    if not path.is_relative_to(root):
        raise DatasetNotFoundError("dataset database resolves outside the dataset root")
    if not path.is_file():
        raise DatasetNotFoundError(f"no dataset database at {DATABASE_RELATIVE_PATH}")
    return path


def read_snapshot(dataset_root: Path, *, attempts: int = 2) -> DatasetSnapshot:
    """Copy, validate, and read the whole dataset. Raises :class:`DatasetError`."""
    source_path = database_path(dataset_root)
    last_error: SnapshotChangedError | None = None
    for _ in range(max(attempts, 1)):
        with tempfile.TemporaryDirectory(prefix="hc-health-records-") as work:
            copy_path = Path(work) / "snapshot.db"
            try:
                _copy_consistently(source_path, copy_path)
            except SnapshotChangedError as exc:
                last_error = exc
                continue
            return _read_copy(copy_path)
    assert last_error is not None
    raise last_error


def _connect_read_only(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path))}?mode=ro"
    try:
        return sqlite3.connect(uri, uri=True)
    except sqlite3.Error as exc:
        raise DatasetNotFoundError("dataset database cannot be opened read-only") from exc


def _generated_at(connection: sqlite3.Connection) -> str | None:
    try:
        row = connection.execute(
            "SELECT value FROM dataset_meta WHERE key = 'generated_at'"
        ).fetchone()
    except sqlite3.Error as exc:
        raise ContractViolationError("dataset_meta is missing or unreadable") from exc
    return None if row is None else str(row[0])


def _copy_consistently(source_path: Path, copy_path: Path) -> None:
    source = _connect_read_only(source_path)
    try:
        before = _generated_at(source)
        target = sqlite3.connect(copy_path)
        try:
            source.backup(target)
        except sqlite3.Error as exc:
            raise SnapshotChangedError("dataset changed or locked during copy") from exc
        finally:
            target.close()
        after = _generated_at(source)
    finally:
        source.close()
    if before != after:
        raise SnapshotChangedError("dataset_meta.generated_at changed during copy")


def _read_copy(copy_path: Path) -> DatasetSnapshot:
    digest = hashlib.sha256(copy_path.read_bytes()).hexdigest()
    connection = sqlite3.connect(copy_path)
    connection.row_factory = sqlite3.Row
    try:
        meta = {
            str(row["key"]): ("" if row["value"] is None else str(row["value"]))
            for row in connection.execute("SELECT key, value FROM dataset_meta")
        }
        version_text = meta.get("schema_version", "")
        try:
            version = int(version_text)
        except ValueError as exc:
            raise UnsupportedSchemaError("dataset schema_version is missing") from exc
        if version != SUPPORTED_SCHEMA_VERSION:
            raise UnsupportedSchemaError(
                f"dataset schema_version {version} is not supported "
                f"(this HealthCurve reads version {SUPPORTED_SCHEMA_VERSION})"
            )
        generated_at = meta.get("generated_at", "").strip()
        if not generated_at:
            raise ContractViolationError("dataset_meta.generated_at is missing")
        exported = {
            key.removeprefix(EXPORTED_AT_PREFIX): value
            for key, value in meta.items()
            if key.startswith(EXPORTED_AT_PREFIX)
        }
        rows = {table: _read_table(connection, table) for table in DatasetTable}
    except sqlite3.Error as exc:
        raise ContractViolationError("dataset could not be read") from exc
    finally:
        connection.close()
    return DatasetSnapshot(
        schema_version=version,
        generated_at=generated_at,
        source_exported_at=dict(sorted(exported.items())),
        sha256=digest,
        rows=rows,
    )


def _read_table(connection: sqlite3.Connection, table: DatasetTable) -> list[dict[str, Any]]:
    # ``table`` is a member of a closed enum, never caller input.
    columns = {
        str(row["name"]) for row in connection.execute(f'PRAGMA table_info("{table.value}")')
    }
    if not columns:
        raise ContractViolationError(f"dataset table {table.value} is missing")
    missing = (REQUIRED_COLUMNS[table] | _PROVENANCE) - columns
    if missing:
        raise ContractViolationError(
            f"dataset table {table.value} lacks columns: {', '.join(sorted(missing))}"
        )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in connection.execute(f'SELECT * FROM "{table.value}"'):  # noqa: S608
        record = {key: row[key] for key in row.keys()}
        record_id = record.get("record_id")
        if not isinstance(record_id, str) or not record_id.strip():
            raise ContractViolationError(f"{table.value} has a row without record_id")
        if len(record_id) > 128:
            raise ContractViolationError(f"{table.value} has an over-long record_id")
        if record_id in seen:
            raise ContractViolationError(f"{table.value} repeats a record_id")
        seen.add(record_id)
        for column in ("source", "organization"):
            value = record.get(column)
            if not isinstance(value, str) or not value.strip():
                raise ContractViolationError(f"{table.value} has a row without {column}")
        rows.append(record)
    return rows
