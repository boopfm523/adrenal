"""Deterministic CSV lab preview; no database writes occur in this module."""

from __future__ import annotations

import csv
import hashlib
import io
import json
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Final

from pydantic import BaseModel, Field, ValidationError, model_validator

from healthcurve.events.timekeeping import EventTime, resolve_event_time
from healthcurve.labs.normalization import normalize_lab_value

MAX_CSV_BYTES: Final = 2 * 1024 * 1024
MAX_CSV_ROWS: Final = 1_000
ALLOWED_COLUMNS: Final = frozenset(
    {"analyte", "value", "qualitative", "unit", "reference_range", "abnormal_flag"}
)


class LabImportError(RuntimeError):
    pass


class CsvColumnMapping(BaseModel):
    analyte: str = Field(min_length=1, max_length=255)
    value: str | None = Field(default=None, min_length=1, max_length=255)
    qualitative: str | None = Field(default=None, min_length=1, max_length=255)
    unit: str | None = Field(default=None, min_length=1, max_length=255)
    reference_range: str | None = Field(default=None, min_length=1, max_length=255)
    abnormal_flag: str | None = Field(default=None, min_length=1, max_length=255)

    @model_validator(mode="after")
    def result_column_present(self) -> CsvColumnMapping:
        if self.value is None and self.qualitative is None:
            raise ValueError("value or qualitative mapping is required")
        headers = [value for value in self.model_dump().values() if value is not None]
        if len(headers) != len(set(headers)):
            raise ValueError("one CSV column cannot map to multiple fields")
        return self


class LabCandidate(BaseModel):
    source_row_index: int
    source_page_number: int | None = None
    analyte_name: str
    original_value: str | None = None
    qualitative_result: str | None = None
    original_unit: str | None = None
    original_reference_range: str | None = None
    abnormal_flag: str | None = None
    normalized_analyte_code: str | None = None
    normalized_value: Decimal | None = None
    normalized_unit: str | None = None
    normalization_method: str | None = None
    flags: list[str] = Field(default_factory=list)


@dataclass(frozen=True)
class ParsedLabImport:
    source_name: str
    source_sha256: str
    mapping_sha256: str
    specimen_time: EventTime
    report_time: EventTime
    candidates: tuple[LabCandidate, ...]


def parse_csv_import(
    *,
    source_name: str | None,
    payload: bytes,
    mapping_json: str,
    analyte_map_json: str,
    specimen_local: datetime,
    report_local: datetime,
    timezone: str,
) -> ParsedLabImport:
    if not payload or len(payload) > MAX_CSV_BYTES:
        raise LabImportError("csv_size_invalid")
    try:
        mapping = CsvColumnMapping.model_validate_json(mapping_json)
        raw_analyte_map = json.loads(analyte_map_json or "{}")
    except (ValidationError, json.JSONDecodeError) as exc:
        raise LabImportError("csv_mapping_invalid") from exc
    if not isinstance(raw_analyte_map, dict) or not all(
        isinstance(key, str)
        and isinstance(value, str)
        and bool(key.strip())
        and bool(value.strip())
        for key, value in raw_analyte_map.items()
    ):
        raise LabImportError("analyte_mapping_invalid")
    try:
        decoded = payload.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise LabImportError("csv_encoding_invalid") from exc
    reader = csv.DictReader(io.StringIO(decoded, newline=""))
    headers = reader.fieldnames or []
    mapped_headers = [value for value in mapping.model_dump().values() if value is not None]
    if not headers or any(header not in headers for header in mapped_headers):
        raise LabImportError("csv_column_missing")

    analyte_index: dict[str, set[str]] = {}
    for source, code in raw_analyte_map.items():
        analyte_index.setdefault(source.strip().casefold(), set()).add(code.strip())

    candidates: list[LabCandidate] = []
    for row_index, row in enumerate(reader, start=2):
        if len(candidates) >= MAX_CSV_ROWS:
            raise LabImportError("csv_row_limit_exceeded")
        analyte = (row.get(mapping.analyte) or "").strip()
        flags: list[str] = []
        codes = analyte_index.get(analyte.casefold(), set()) if analyte else set()
        if not analyte or not codes:
            flags.append("unrecognized_analyte")
        elif len(codes) > 1:
            flags.append("ambiguous_analyte")
        candidate = LabCandidate(
            source_row_index=row_index,
            analyte_name=analyte,
            original_value=_cell(row, mapping.value),
            qualitative_result=_cell(row, mapping.qualitative),
            original_unit=_cell(row, mapping.unit),
            original_reference_range=_cell(row, mapping.reference_range),
            abnormal_flag=_cell(row, mapping.abnormal_flag),
            normalized_analyte_code=next(iter(codes)) if len(codes) == 1 else None,
            flags=flags,
        )
        normalized = normalize_lab_value(
            candidate.analyte_name, candidate.original_value, candidate.original_unit
        )
        if normalized is not None:
            candidate.normalized_analyte_code = normalized.analyte_code
            candidate.normalized_value = normalized.value
            candidate.normalized_unit = normalized.unit
            candidate.normalization_method = normalized.method
            candidate.flags = [
                flag
                for flag in candidate.flags
                if flag not in {"unrecognized_analyte", "ambiguous_analyte"}
            ]
        if candidate.original_value is None and candidate.qualitative_result is None:
            candidate.flags.append("missing_result")
        candidates.append(candidate)
    if not candidates:
        raise LabImportError("csv_has_no_rows")

    mapping_canonical = json.dumps(
        {"columns": mapping.model_dump(), "analytes": raw_analyte_map},
        sort_keys=True,
        separators=(",", ":"),
    )
    try:
        specimen_time = resolve_event_time(specimen_local, timezone)
        report_time = resolve_event_time(report_local, timezone)
    except (ValueError, KeyError) as exc:
        raise LabImportError("lab_time_invalid") from exc
    if report_time.occurred_at < specimen_time.occurred_at:
        raise LabImportError("report_before_specimen")
    return ParsedLabImport(
        source_name=(source_name or "upload.csv")[:255],
        source_sha256=hashlib.sha256(payload).hexdigest(),
        mapping_sha256=hashlib.sha256(mapping_canonical.encode()).hexdigest(),
        specimen_time=specimen_time,
        report_time=report_time,
        candidates=tuple(candidates),
    )


def _cell(row: dict[str, str | None], header: str | None) -> str | None:
    if header is None:
        return None
    value = row.get(header)
    return value if value not in {None, ""} else None
