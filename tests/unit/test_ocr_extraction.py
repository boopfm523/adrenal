"""Bounded OCR fallback with synthetic, image-only PDF inputs."""

from __future__ import annotations

import hashlib
import uuid
from pathlib import Path

import pytest

from healthcurve.labs.ocr_extraction import (
    OcrError,
    PreviewError,
    extract_textless_pages,
    render_review_previews,
)
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


def test_unresolved_ocr_page_publishes_only_bounded_inert_preview(tmp_path: Path) -> None:
    path, embedded = _embedded_scan(tmp_path)
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\t"
        "height\tconf\ttext\n1\t1\t1\t1\t1\t1\t80\t100\t170\t45\t42\tUnclear"
    )
    published: list[tuple[int, bytes]] = []

    result = extract_textless_pages(
        path,
        embedded=embedded,
        runner=OcrToolRunner(tsv=tsv),
        preview_sink=lambda page, preview: published.append((page, preview.read_bytes())),
    )

    assert result.vision_pages == [1]
    assert published[0][0] == 1
    assert published[0][1].startswith(b"\x89PNG\r\n\x1a\n")


def test_review_previews_cover_every_validated_page_with_inert_png(tmp_path: Path) -> None:
    path, _embedded = _embedded_scan(tmp_path)
    runner = OcrToolRunner()
    published: list[tuple[int, bytes]] = []

    render_review_previews(
        path,
        page_numbers=[3, 1, 2, 2],
        runner=runner,
        preview_sink=lambda page, preview: published.append((page, preview.read_bytes())),
    )

    assert [page for page, _payload in published] == [1, 2, 3]
    assert all(payload.startswith(b"\x89PNG\r\n\x1a\n") for _page, payload in published)
    render_calls = [call for call in runner.calls if call[0] == "pdftoppm"]
    assert len(render_calls) == 3
    assert all(int(call[call.index("-scale-to") + 1]) <= 2400 for call in render_calls)


def test_review_preview_pixel_limit_fails_closed_and_purges_scratch(tmp_path: Path) -> None:
    path, _embedded = _embedded_scan(tmp_path)
    runner = OcrToolRunner(width=2401, height=100)

    with pytest.raises(PreviewError, match="preview_pixel_limit_exceeded"):
        render_review_previews(
            path,
            page_numbers=[1],
            runner=runner,
            preview_sink=lambda _page, _preview: None,
        )

    scratch_paths = [Path(call[-1]).parent for call in runner.calls if call[0] == "pdftoppm"]
    assert scratch_paths and all(not path.exists() for path in scratch_paths)
