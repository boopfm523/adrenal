"""Deterministic laboratory CSV preview behavior."""

from __future__ import annotations

import json
from datetime import datetime

import pytest

from healthcurve.labs.imports import LabImportError, parse_csv_import


def _parse(
    payload: bytes,
    *,
    mapping: dict[str, str] | None = None,
    analytes: dict[str, str] | None = None,
):
    return parse_csv_import(
        source_name="synthetic.csv",
        payload=payload,
        mapping_json=json.dumps(mapping or {"analyte": "Test", "value": "Result"}),
        analyte_map_json=json.dumps(analytes or {}),
        specimen_local=datetime(2026, 8, 8, 9, 0),  # noqa: DTZ001
        report_local=datetime(2026, 8, 8, 10, 0),  # noqa: DTZ001
        timezone="Europe/London",
    )


def test_preview_preserves_source_cells_and_only_exact_mapping_normalizes() -> None:
    parsed = _parse(
        b"Test,Result,Unit\nSynthetic marker, 12.30 , synthetic units \nUnknown,7,x\n",
        mapping={"analyte": "Test", "value": "Result", "unit": "Unit"},
        analytes={"Synthetic marker": "synthetic-marker"},
    )

    assert parsed.candidates[0].original_value == " 12.30 "
    assert parsed.candidates[0].original_unit == " synthetic units "
    assert parsed.candidates[0].normalized_analyte_code == "synthetic-marker"
    assert parsed.candidates[0].flags == []
    assert parsed.candidates[1].normalized_analyte_code is None
    assert parsed.candidates[1].flags == ["unrecognized_analyte"]


def test_casefold_collision_is_flagged_ambiguous_instead_of_guessed() -> None:
    parsed = _parse(
        b"Test,Result\nSYNTHETIC MARKER,4\n",
        analytes={"Synthetic Marker": "code-a", "synthetic marker": "code-b"},
    )

    assert parsed.candidates[0].normalized_analyte_code is None
    assert parsed.candidates[0].flags == ["ambiguous_analyte"]


@pytest.mark.parametrize(
    "mapping, analytes, expected",
    [
        ({"analyte": "Test", "value": "Test"}, {}, "csv_mapping_invalid"),
        ({"analyte": "Missing", "value": "Result"}, {}, "csv_column_missing"),
        ({"analyte": "Test", "value": "Result"}, {"Test": " "}, "analyte_mapping_invalid"),
    ],
)
def test_invalid_mapping_fails_closed(
    mapping: dict[str, str], analytes: dict[str, str], expected: str
) -> None:
    with pytest.raises(LabImportError, match=expected):
        _parse(b"Test,Result\nSynthetic marker,4\n", mapping=mapping, analytes=analytes)


def test_preview_flags_missing_source_result() -> None:
    parsed = _parse(b"Test,Result\nSynthetic marker,\n")

    assert "missing_result" in parsed.candidates[0].flags
