"""Validated filesystem-mailbox schemas shared by API and document workers."""

from __future__ import annotations

import uuid
from typing import Final, Literal

from pydantic import BaseModel, Field, model_validator

EXTRACTION_SCHEMA_VERSION: Final = "lab-pdf-v2"
MAX_EXTRACTED_ROWS: Final = 2_000
MAX_SOURCE_LINE_CHARS: Final = 2_000


class PdfDraftCandidate(BaseModel):
    page_number: int = Field(ge=1, le=100)
    row_index: int = Field(ge=1)
    extraction_tier: Literal["embedded_text", "ocr"] = "embedded_text"
    coordinate_space: Literal["pdf_points", "rendered_pixels"] = "pdf_points"
    parsed: bool
    analyte_name: str | None = None
    original_value: str | None = None
    original_unit: str | None = None
    original_reference_range: str | None = None
    source_text: str = Field(max_length=MAX_SOURCE_LINE_CHARS)
    x0: float = Field(allow_inf_nan=False)
    top: float = Field(allow_inf_nan=False)
    x1: float = Field(allow_inf_nan=False)
    bottom: float = Field(allow_inf_nan=False)
    confidence: float = Field(ge=0, le=1, allow_inf_nan=False)
    flags: list[str] = Field(default_factory=list, max_length=16)
    requires_confirmation: Literal[True] = True

    @model_validator(mode="after")
    def fields_match_parse_state(self) -> PdfDraftCandidate:
        if self.x1 < self.x0 or self.bottom < self.top:
            raise ValueError("candidate bounding box is invalid")
        if self.parsed and (not self.analyte_name or not self.original_value):
            raise ValueError("parsed candidate requires analyte and value")
        if not self.parsed and any(
            value is not None
            for value in (
                self.analyte_name,
                self.original_value,
                self.original_unit,
                self.original_reference_range,
            )
        ):
            raise ValueError("unparsed candidate cannot contain guessed fields")
        return self


class EmbeddedExtractionResult(BaseModel):
    document_id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    schema_version: Literal["lab-pdf-v1", "lab-pdf-v2"] = EXTRACTION_SCHEMA_VERSION
    extractor_name: str = Field(default="pdfplumber", min_length=1, max_length=64)
    extractor_version: str = Field(min_length=1, max_length=64)
    extraction_tier: Literal["embedded_text", "ocr", "mixed"] = "embedded_text"
    textless_pages: list[int] = Field(default_factory=list, max_length=100)
    ocr_pages: list[int] = Field(default_factory=list, max_length=100)
    page_count: int = Field(ge=1, le=100)
    parsed_count: int = Field(ge=0)
    unparsed_count: int = Field(ge=0)
    adequate: bool
    candidates: list[PdfDraftCandidate] = Field(max_length=MAX_EXTRACTED_ROWS)

    @model_validator(mode="after")
    def counts_match_candidates(self) -> EmbeddedExtractionResult:
        parsed = sum(candidate.parsed for candidate in self.candidates)
        unparsed = len(self.candidates) - parsed
        if (self.parsed_count, self.unparsed_count) != (parsed, unparsed):
            raise ValueError("extraction counts do not match candidates")
        if self.adequate and parsed == 0:
            raise ValueError("adequate extraction requires at least one parsed row")
        if any(
            page < 1 or page > self.page_count for page in (*self.textless_pages, *self.ocr_pages)
        ):
            raise ValueError("extraction page list is outside document bounds")
        return self
