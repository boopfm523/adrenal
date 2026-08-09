"""Conservative embedded-text extraction for validated laboratory PDFs.

This module performs no model calls and has no fact-write path. It turns explicit table
columns into review candidates and retains every other non-empty line as unparsed
evidence instead of guessing or silently dropping it.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from pathlib import Path
from typing import Final

import pdfplumber

from healthcurve.labs.pdf_schemas import (
    MAX_EXTRACTED_ROWS,
    MAX_SOURCE_LINE_CHARS,
    EmbeddedExtractionResult,
    PdfDraftCandidate,
)

EMBEDDED_EXTRACTOR: Final = "pdfplumber"
EMBEDDED_EXTRACTOR_VERSION: Final = pdfplumber.__version__

_ANALYTE_HEADERS = frozenset({"analyte", "test", "component"})
_VALUE_HEADERS = frozenset({"value", "result"})
_UNIT_HEADERS = frozenset({"unit", "units"})
_RANGE_HEADERS = frozenset({"range", "reference", "interval"})


def extract_embedded_text(
    path: Path, *, document_id: uuid.UUID, sha256: str
) -> EmbeddedExtractionResult:
    candidates: list[PdfDraftCandidate] = []
    with pdfplumber.open(path) as pdf:
        if not 1 <= len(pdf.pages) <= 100:
            raise ValueError("pdf_page_limit_exceeded")
        for page_number, page in enumerate(pdf.pages, start=1):
            words = page.extract_words(
                keep_blank_chars=False,
                use_text_flow=False,
                x_tolerance=2,
                y_tolerance=3,
            )
            candidates.extend(_page_candidates(words, page_number=page_number))
            if len(candidates) > MAX_EXTRACTED_ROWS:
                raise ValueError("pdf_extracted_row_limit_exceeded")

        parsed_count = sum(candidate.parsed for candidate in candidates)
        unparsed_count = len(candidates) - parsed_count
        return EmbeddedExtractionResult(
            document_id=document_id,
            sha256=sha256,
            extractor_version=EMBEDDED_EXTRACTOR_VERSION,
            page_count=len(pdf.pages),
            parsed_count=parsed_count,
            unparsed_count=unparsed_count,
            # A deterministic table header plus at least one explicit row is enough
            # to offer a draft. Unparsed evidence remains visible alongside it.
            adequate=parsed_count > 0,
            candidates=candidates,
        )


def _page_candidates(
    words: list[dict[str, object]], *, page_number: int
) -> list[PdfDraftCandidate]:
    lines = _group_lines(words)
    header_index: int | None = None
    columns: dict[str, float] = {}
    for index, line in enumerate(lines):
        columns = _header_columns(line)
        if "analyte" in columns and "value" in columns:
            header_index = index
            break

    candidates: list[PdfDraftCandidate] = []
    for row_index, line in enumerate(lines, start=1):
        source_text = " ".join(str(word["text"]) for word in line)[:MAX_SOURCE_LINE_CHARS]
        bounds = _bounds(line)
        if header_index is not None and row_index - 1 == header_index:
            continue
        cells = _partition(line, columns) if header_index is not None else {}
        analyte = cells.get("analyte")
        value = cells.get("value")
        parsed = bool(analyte and value)
        flags = [] if parsed else ["unparsed_row"]
        candidates.append(
            PdfDraftCandidate(
                page_number=page_number,
                row_index=row_index,
                parsed=parsed,
                analyte_name=analyte if parsed else None,
                original_value=value if parsed else None,
                original_unit=cells.get("unit") if parsed else None,
                original_reference_range=cells.get("range") if parsed else None,
                source_text=source_text,
                x0=bounds[0],
                top=bounds[1],
                x1=bounds[2],
                bottom=bounds[3],
                confidence=1.0 if parsed else 0.0,
                flags=flags,
            )
        )
    return candidates


def _group_lines(words: Iterable[dict[str, object]]) -> list[list[dict[str, object]]]:
    ordered = sorted(words, key=lambda word: (_coordinate(word["top"]), _coordinate(word["x0"])))
    lines: list[list[dict[str, object]]] = []
    for word in ordered:
        if not lines or abs(_coordinate(word["top"]) - _coordinate(lines[-1][0]["top"])) > 3:
            lines.append([word])
        else:
            lines[-1].append(word)
    for line in lines:
        line.sort(key=lambda word: _coordinate(word["x0"]))
    return lines


def _header_columns(line: list[dict[str, object]]) -> dict[str, float]:
    columns: dict[str, float] = {}
    for word in line:
        normalized = str(word["text"]).strip().casefold().rstrip(":")
        target: str | None = None
        if normalized in _ANALYTE_HEADERS:
            target = "analyte"
        elif normalized in _VALUE_HEADERS:
            target = "value"
        elif normalized in _UNIT_HEADERS:
            target = "unit"
        elif normalized in _RANGE_HEADERS:
            target = "range"
        if target is not None and target not in columns:
            columns[target] = _coordinate(word["x0"])
    return columns


def _partition(line: list[dict[str, object]], columns: dict[str, float]) -> dict[str, str]:
    ordered_columns = sorted(columns.items(), key=lambda item: item[1])
    cells: dict[str, list[str]] = {name: [] for name, _ in ordered_columns}
    for word in line:
        x0 = _coordinate(word["x0"])
        selected = ordered_columns[0][0]
        for name, start in ordered_columns:
            if x0 >= start - 3:
                selected = name
            else:
                break
        cells[selected].append(str(word["text"]))
    return {name: " ".join(values) for name, values in cells.items() if values}


def _bounds(line: list[dict[str, object]]) -> tuple[float, float, float, float]:
    return (
        min(_coordinate(word["x0"]) for word in line),
        min(_coordinate(word["top"]) for word in line),
        max(_coordinate(word["x1"]) for word in line),
        max(_coordinate(word["bottom"]) for word in line),
    )


def _coordinate(value: object) -> float:
    if isinstance(value, (int, float, str)):
        return float(value)
    raise ValueError("pdf_coordinate_invalid")
