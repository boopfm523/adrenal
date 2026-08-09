"""Deterministic embedded-text PDF extraction tests with synthetic content."""

from __future__ import annotations

import hashlib
import io
import json
import uuid
from pathlib import Path

import pytest

from healthcurve.document_worker import process_available
from healthcurve.labs.documents import (
    DocumentLayout,
    DocumentStorageError,
    load_extraction_result,
    store_pdf_upload,
)
from healthcurve.labs.pdf_extraction import extract_embedded_text
from tests.fixtures.pdf import QpdfRunner, synthetic_text_lab_pdf


def test_digital_lab_table_becomes_review_candidates_without_a_model(tmp_path: Path) -> None:
    payload = synthetic_text_lab_pdf()
    path = tmp_path / "synthetic.pdf"
    path.write_bytes(payload)
    document_id = uuid.uuid4()

    result = extract_embedded_text(
        path,
        document_id=document_id,
        sha256=hashlib.sha256(payload).hexdigest(),
    )

    assert result.extraction_tier == "embedded_text"
    assert result.extractor_name == "pdfplumber"
    assert result.extractor_version
    assert result.adequate is True
    parsed = [candidate for candidate in result.candidates if candidate.parsed]
    assert len(parsed) == 1
    assert parsed[0].analyte_name == "Synthetic sodium"
    assert parsed[0].original_value == "140"
    assert parsed[0].original_unit == "mmol/L"
    assert parsed[0].original_reference_range == "135-145"
    assert parsed[0].requires_confirmation is True
    unparsed = [candidate for candidate in result.candidates if not candidate.parsed]
    assert {candidate.source_text for candidate in unparsed} == {
        "Synthetic laboratory panel",
        "Synthetic unparsed note",
    }
    assert all(candidate.flags == ["unparsed_row"] for candidate in unparsed)


def test_networkless_worker_publishes_extraction_mailbox_after_validation(tmp_path: Path) -> None:
    layout = DocumentLayout(tmp_path / "uploads")
    payload = synthetic_text_lab_pdf()
    upload = store_pdf_upload(
        io.BytesIO(payload),
        layout=layout,
        submitted_name="synthetic.pdf",
        media_type="application/pdf",
    )

    assert process_available(layout, runner=QpdfRunner()) == 1

    extraction = load_extraction_result(layout, upload.document_id)
    assert extraction is not None
    assert extraction.parsed_count == 1
    assert extraction.unparsed_count == 2
    assert layout.path("stored", upload.document_id).read_bytes() == payload


def test_tampered_extraction_mailbox_fails_closed(tmp_path: Path) -> None:
    layout = DocumentLayout(tmp_path / "uploads")
    layout.prepare()
    document_id = uuid.uuid4()
    layout.path("extractions", document_id, ".json").write_text(
        json.dumps(
            {
                "document_id": str(document_id),
                "sha256": "0" * 64,
                "extractor_version": "0.11.9",
                "page_count": 1,
                "parsed_count": 1,
                "unparsed_count": 0,
                "adequate": True,
                "candidates": [],
            }
        )
    )

    with pytest.raises(DocumentStorageError, match="document_extraction_result_invalid"):
        load_extraction_result(layout, document_id)
