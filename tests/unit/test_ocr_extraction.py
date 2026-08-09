"""Bounded OCR fallback with synthetic, image-only PDF inputs."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from healthcurve.labs.ocr_extraction import OcrError, extract_textless_pages
from healthcurve.labs.pdf_extraction import extract_embedded_text
from tests.fixtures.pdf import OcrToolRunner, synthetic_scanned_lab_pdf


def _embedded_scan(tmp_path: Path):
    payload = synthetic_scanned_lab_pdf()
    path = tmp_path / "synthetic-scan.pdf"
    path.write_bytes(payload)
    embedded = extract_embedded_text(
        path,
        document_id=uuid.uuid4(),
        sha256=hashlib.sha256(payload).hexdigest(),
    )
    assert embedded.candidates == []
    assert embedded.textless_pages == [1]
    return path, embedded


def test_scanned_page_uses_ocr_and_preserves_boxes_confidence_and_unparsed(
    tmp_path: Path,
) -> None:
    path, embedded = _embedded_scan(tmp_path)
    runner = OcrToolRunner()

    result = extract_textless_pages(path, embedded=embedded, runner=runner)

    assert result.extraction_tier == "ocr"
    assert result.ocr_pages == [1]
    assert result.adequate is True
    parsed = [candidate for candidate in result.candidates if candidate.parsed]
    assert len(parsed) == 1
    assert parsed[0].analyte_name == "Synthetic sodium"
    assert parsed[0].original_value == "140"
    assert parsed[0].original_unit == "mmol/L"
    assert parsed[0].original_reference_range == "135-145"
    assert parsed[0].coordinate_space == "rendered_pixels"
    assert parsed[0].confidence == pytest.approx(0.93)
    unparsed = [candidate for candidate in result.candidates if not candidate.parsed]
    assert unparsed[0].source_text == "Unclear note"
    assert set(unparsed[0].flags) == {"unparsed_row", "low_confidence"}
    scratch_paths = [Path(call[-1]).parent for call in runner.calls if call[0] == "pdftoppm"]
    assert scratch_paths and all(not path.exists() for path in scratch_paths)


def test_render_dimension_cap_fails_closed_and_purges_scratch(tmp_path: Path) -> None:
    path, embedded = _embedded_scan(tmp_path)
    runner = OcrToolRunner(width=2401, height=100)

    with pytest.raises(OcrError, match="ocr_pixel_limit_exceeded"):
        extract_textless_pages(path, embedded=embedded, runner=runner)

    scratch_paths = [Path(call[-1]).parent for call in runner.calls if call[0] == "pdftoppm"]
    assert scratch_paths and all(not path.exists() for path in scratch_paths)


def test_document_wall_clock_cap_stops_before_ocr(tmp_path: Path) -> None:
    path, embedded = _embedded_scan(tmp_path)
    ticks = iter((0.0, 0.0, 121.0))

    with pytest.raises(OcrError, match="ocr_document_timeout"):
        extract_textless_pages(
            path,
            embedded=embedded,
            runner=OcrToolRunner(),
            clock=lambda: next(ticks),
        )
