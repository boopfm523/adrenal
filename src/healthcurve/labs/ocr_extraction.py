"""Bounded Poppler/Tesseract fallback for textless PDF pages (ADR-0010)."""

from __future__ import annotations

import csv
import math
import time
from collections.abc import Callable, Iterable
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import Final, Protocol

from PIL import Image

from healthcurve.labs.documents import MAX_PDF_PAGES
from healthcurve.labs.pdf_extraction import candidates_from_words
from healthcurve.labs.pdf_schemas import EmbeddedExtractionResult, PdfDraftCandidate

POPPLER_VERSION: Final = "22.12.0"
TESSERACT_VERSION: Final = "5.3.0"
MAX_RENDER_DIMENSION: Final = 2_400
MAX_PAGE_PIXELS: Final = MAX_RENDER_DIMENSION * MAX_RENDER_DIMENSION
MAX_DOCUMENT_PIXELS: Final = 100_000_000
MAX_DOCUMENT_SECONDS: Final = 120.0
MAX_COMMAND_SECONDS: Final = 30.0
MAX_TSV_BYTES: Final = 4 * 1024 * 1024


class CommandOutcome(Protocol):
    @property
    def returncode(self) -> int: ...

    @property
    def stdout(self) -> str: ...


class OcrRunner(Protocol):
    def __call__(
        self, args: list[str], *, timeout: float, stdout_path: Path | None = None
    ) -> CommandOutcome: ...


def extract_textless_pages(
    path: Path,
    *,
    embedded: EmbeddedExtractionResult,
    runner: OcrRunner,
    clock: Callable[[], float] = time.monotonic,
    preview_sink: Callable[[int, Path], None] | None = None,
) -> EmbeddedExtractionResult:
    if not embedded.textless_pages:
        return embedded
    started = clock()
    candidates = list(embedded.candidates)
    total_pixels = 0
    vision_pages: list[int] = []
    with TemporaryDirectory(prefix="hc-ocr-") as scratch_name:
        scratch = Path(scratch_name)
        for page_number in embedded.textless_pages:
            remaining = MAX_DOCUMENT_SECONDS - (clock() - started)
            if remaining <= 0:
                raise OcrError("ocr_document_timeout")
            prefix = scratch / f"page-{page_number}"
            rendered = runner(
                [
                    "pdftoppm",
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-singlefile",
                    "-scale-to",
                    str(MAX_RENDER_DIMENSION),
                    "-png",
                    str(path),
                    str(prefix),
                ],
                timeout=min(MAX_COMMAND_SECONDS, remaining),
            )
            image_path = prefix.with_suffix(".png")
            if rendered.returncode != 0 or not image_path.is_file():
                raise OcrError("ocr_render_failed")
            try:
                with Image.open(image_path) as image:
                    width, height = image.size
                    image.verify()
            except (OSError, ValueError) as exc:
                raise OcrError("ocr_render_invalid") from exc
            pixels = width * height
            total_pixels += pixels
            if (
                width < 1
                or height < 1
                or width > MAX_RENDER_DIMENSION
                or height > MAX_RENDER_DIMENSION
                or pixels > MAX_PAGE_PIXELS
                or total_pixels > MAX_DOCUMENT_PIXELS
            ):
                raise OcrError("ocr_pixel_limit_exceeded")

            remaining = MAX_DOCUMENT_SECONDS - (clock() - started)
            if remaining <= 0:
                raise OcrError("ocr_document_timeout")
            tsv_path = scratch / f"page-{page_number}.tsv"
            recognized = runner(
                [
                    "tesseract",
                    str(image_path),
                    "stdout",
                    "-l",
                    "eng",
                    "--psm",
                    "6",
                    "tsv",
                ],
                timeout=min(MAX_COMMAND_SECONDS, remaining),
                stdout_path=tsv_path,
            )
            if (
                recognized.returncode != 0
                or not tsv_path.is_file()
                or tsv_path.stat().st_size > MAX_TSV_BYTES
            ):
                raise OcrError("ocr_recognition_failed")
            words = _read_tsv(tsv_path)
            page_candidates = candidates_from_words(
                words,
                page_number=page_number,
                extraction_tier="ocr",
            )
            if not page_candidates:
                page_candidates = [_empty_page_candidate(page_number)]
            if not any(
                candidate.parsed and candidate.confidence >= 0.8 for candidate in page_candidates
            ):
                vision_pages.append(page_number)
                if preview_sink is not None:
                    preview_sink(page_number, image_path)
            candidates.extend(page_candidates)

    parsed_count = sum(candidate.parsed for candidate in candidates)
    unparsed_count = len(candidates) - parsed_count
    return EmbeddedExtractionResult(
        document_id=embedded.document_id,
        sha256=embedded.sha256,
        extractor_name="pdfplumber+tesseract",
        extractor_version=(
            f"pdfplumber={embedded.extractor_version};"
            f"poppler={POPPLER_VERSION};tesseract={TESSERACT_VERSION}"
        ),
        extraction_tier="mixed" if embedded.candidates else "ocr",
        page_count=embedded.page_count,
        textless_pages=embedded.textless_pages,
        ocr_pages=embedded.textless_pages,
        vision_pages=vision_pages,
        parsed_count=parsed_count,
        unparsed_count=unparsed_count,
        adequate=any(candidate.parsed and candidate.confidence >= 0.8 for candidate in candidates),
        candidates=candidates,
    )


class OcrError(RuntimeError):
    pass


class PreviewError(RuntimeError):
    """A stable, content-free failure while producing inert review evidence."""


def render_review_previews(
    path: Path,
    *,
    page_numbers: Iterable[int],
    runner: OcrRunner,
    preview_sink: Callable[[int, Path], None],
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Render bounded inert PNG evidence for every page represented by a draft.

    The scale is reduced for unusually long documents so the existing per-page and
    per-document pixel ceilings remain true even when all 100 allowed pages need
    review evidence. Raw PDFs are never exposed to the browser (ADR-0010).
    """
    pages = sorted(set(page_numbers))
    if not pages:
        return
    if pages[0] < 1 or pages[-1] > MAX_PDF_PAGES:
        raise PreviewError("preview_page_invalid")
    scale_to = min(MAX_RENDER_DIMENSION, math.isqrt(MAX_DOCUMENT_PIXELS // len(pages)))
    if scale_to < 1:
        raise PreviewError("preview_pixel_limit_exceeded")
    started = clock()
    total_pixels = 0
    with TemporaryDirectory(prefix="hc-preview-") as scratch_name:
        scratch = Path(scratch_name)
        for page_number in pages:
            remaining = MAX_DOCUMENT_SECONDS - (clock() - started)
            if remaining <= 0:
                raise PreviewError("preview_document_timeout")
            prefix = scratch / f"page-{page_number}"
            rendered = runner(
                [
                    "pdftoppm",
                    "-f",
                    str(page_number),
                    "-l",
                    str(page_number),
                    "-singlefile",
                    "-scale-to",
                    str(scale_to),
                    "-png",
                    str(path),
                    str(prefix),
                ],
                timeout=min(MAX_COMMAND_SECONDS, remaining),
            )
            image_path = prefix.with_suffix(".png")
            if rendered.returncode != 0 or not image_path.is_file():
                raise PreviewError("preview_render_failed")
            try:
                with Image.open(image_path) as image:
                    width, height = image.size
                    image.verify()
            except (OSError, ValueError) as exc:
                raise PreviewError("preview_render_invalid") from exc
            pixels = width * height
            total_pixels += pixels
            if (
                width < 1
                or height < 1
                or width > MAX_RENDER_DIMENSION
                or height > MAX_RENDER_DIMENSION
                or pixels > MAX_PAGE_PIXELS
                or total_pixels > MAX_DOCUMENT_PIXELS
            ):
                raise PreviewError("preview_pixel_limit_exceeded")
            preview_sink(page_number, image_path)


def failed_ocr_result(
    embedded: EmbeddedExtractionResult, *, reason_code: str
) -> EmbeddedExtractionResult:
    candidates = list(embedded.candidates)
    for page_number in embedded.textless_pages:
        candidates.append(
            PdfDraftCandidate(
                page_number=page_number,
                row_index=1,
                extraction_tier="ocr",
                coordinate_space="rendered_pixels",
                parsed=False,
                source_text="",
                x0=0,
                top=0,
                x1=0,
                bottom=0,
                confidence=0,
                flags=[reason_code, "unparsed_row", "low_confidence"],
            )
        )
    parsed_count = sum(candidate.parsed for candidate in candidates)
    return EmbeddedExtractionResult(
        document_id=embedded.document_id,
        sha256=embedded.sha256,
        extractor_name="pdfplumber+tesseract",
        extractor_version=(
            f"pdfplumber={embedded.extractor_version};"
            f"poppler={POPPLER_VERSION};tesseract={TESSERACT_VERSION}"
        ),
        extraction_tier="mixed" if embedded.candidates else "ocr",
        page_count=embedded.page_count,
        textless_pages=embedded.textless_pages,
        ocr_pages=embedded.textless_pages,
        vision_pages=embedded.textless_pages,
        parsed_count=parsed_count,
        unparsed_count=len(candidates) - parsed_count,
        adequate=embedded.adequate,
        candidates=candidates,
    )


def _read_tsv(path: Path) -> list[dict[str, object]]:
    words: list[dict[str, object]] = []
    with path.open(encoding="utf-8", newline="") as source:
        reader = csv.DictReader(source, delimiter="\t")
        required = {
            "page_num",
            "block_num",
            "par_num",
            "line_num",
            "left",
            "top",
            "width",
            "height",
            "conf",
            "text",
        }
        if reader.fieldnames is None or not required.issubset(reader.fieldnames):
            raise OcrError("ocr_tsv_invalid")
        for row in reader:
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                left = int(row["left"])
                top = int(row["top"])
                width = int(row["width"])
                height = int(row["height"])
                confidence = float(row["conf"]) / 100
            except (KeyError, TypeError, ValueError) as exc:
                raise OcrError("ocr_tsv_invalid") from exc
            if min(left, top, width, height) < 0 or width == 0 or height == 0:
                raise OcrError("ocr_tsv_invalid")
            words.append(
                {
                    "text": text,
                    "x0": left,
                    "top": top,
                    "x1": left + width,
                    "bottom": top + height,
                    "confidence": confidence,
                    "line_key": (
                        row.get("page_num"),
                        row.get("block_num"),
                        row.get("par_num"),
                        row.get("line_num"),
                    ),
                }
            )
    return words


def _empty_page_candidate(page_number: int) -> PdfDraftCandidate:
    return PdfDraftCandidate(
        page_number=page_number,
        row_index=1,
        extraction_tier="ocr",
        coordinate_space="rendered_pixels",
        parsed=False,
        source_text="",
        x0=0,
        top=0,
        x1=0,
        bottom=0,
        confidence=0,
        flags=["ocr_no_text", "unparsed_row", "low_confidence"],
    )
