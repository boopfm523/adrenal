"""Hostile PDF storage and no-network worker boundary tests."""

from __future__ import annotations

import io
import stat
import uuid
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from healthcurve.document_worker import validate_one
from healthcurve.labs.cleanup_jobs import make_document_cleanup_handler
from healthcurve.labs.documents import (
    DocumentLayout,
    DocumentStorageError,
    mark_deleted,
    store_pdf_upload,
)
from healthcurve.operations.jobs import JobQueueError
from tests.fixtures.pdf import QpdfRunner


def _upload(layout: DocumentLayout) -> uuid.UUID:
    upload = store_pdf_upload(
        io.BytesIO(b"%PDF-1.7\nsynthetic fixture only\n"),
        layout=layout,
        submitted_name="../../Synthetic report.pdf",
        media_type="application/pdf",
    )
    return upload.document_id


def test_upload_is_bounded_sniffed_private_and_opaque(tmp_path: Path) -> None:
    layout = DocumentLayout(tmp_path / "uploads")
    upload = store_pdf_upload(
        io.BytesIO(b"%PDF-1.7\nsynthetic fixture only\n"),
        layout=layout,
        submitted_name="../../Synthetic report.pdf",
        media_type="application/pdf",
    )

    path = layout.path("quarantine", upload.document_id)
    assert path.name == f"{upload.document_id}.pdf"
    assert upload.display_name == "Synthetic report.pdf"
    assert stat.S_IMODE(path.stat().st_mode) == 0o600
    assert stat.S_IMODE(layout.root.stat().st_mode) == 0o700

    with pytest.raises(DocumentStorageError, match="pdf_media_type_invalid"):
        store_pdf_upload(
            io.BytesIO(b"%PDF-1.7\n"),
            layout=layout,
            submitted_name="not-trusted.pdf",
            media_type="text/plain",
        )
    with pytest.raises(DocumentStorageError, match="pdf_signature_invalid"):
        store_pdf_upload(
            io.BytesIO(b"not a PDF"),
            layout=layout,
            submitted_name="looks-like.pdf",
            media_type="application/pdf",
        )


def test_upload_size_cap_removes_partial_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    layout = DocumentLayout(tmp_path / "uploads")
    monkeypatch.setattr("healthcurve.labs.documents.MAX_PDF_BYTES", 8)
    with pytest.raises(DocumentStorageError, match="pdf_size_invalid"):
        store_pdf_upload(
            io.BytesIO(b"%PDF-1.7x"),
            layout=layout,
            submitted_name="synthetic.pdf",
            media_type="application/pdf",
        )
    assert list(layout.quarantine.iterdir()) == []


def test_worker_accepts_only_after_page_and_interactive_checks(tmp_path: Path) -> None:
    layout = DocumentLayout(tmp_path / "uploads")
    document_id = _upload(layout)
    runner = QpdfRunner(pages=2)

    result = validate_one(layout, document_id, runner=runner)

    assert result.status == "stored"
    assert result.page_count == 2
    assert layout.path("stored", document_id).is_file()
    assert not layout.path("quarantine", document_id).exists()
    assert [call[1] for call in runner.calls] == ["--check", "--show-npages", "--json-output=2"]


@pytest.mark.parametrize(
    "runner, reason",
    [
        (QpdfRunner(check_code=2), "pdf_structure_invalid"),
        (QpdfRunner(pages=101), "pdf_page_limit_exceeded"),
        (
            QpdfRunner(inspection={"objects": {"1 0 R": {"/OpenAction": {}}}}),
            "pdf_interactive_content_rejected",
        ),
        (
            QpdfRunner(inspection={"attachments": {"payload.bin": {}}}),
            "pdf_interactive_content_rejected",
        ),
    ],
)
def test_worker_rejects_malformed_over_limit_or_active_pdf(
    tmp_path: Path, runner: QpdfRunner, reason: str
) -> None:
    layout = DocumentLayout(tmp_path / str(uuid.uuid4()))
    document_id = _upload(layout)

    result = validate_one(layout, document_id, runner=runner)

    assert result.status == "rejected"
    assert result.reason_code == reason
    assert not layout.path("stored", document_id).exists()
    assert not layout.path("work", document_id).exists()


def test_tombstone_prevents_in_flight_document_from_being_published(tmp_path: Path) -> None:
    layout = DocumentLayout(tmp_path / "uploads")
    document_id = _upload(layout)
    mark_deleted(layout, document_id)

    with pytest.raises(FileNotFoundError):
        validate_one(layout, document_id, runner=QpdfRunner())
    assert not layout.path("stored", document_id).exists()


def test_durable_cleanup_handler_is_idempotent_and_rejects_bad_payload(tmp_path: Path) -> None:
    layout = DocumentLayout(tmp_path / "uploads")
    document_id = _upload(layout)
    handler = make_document_cleanup_handler(layout)

    handler(Mock(spec=Session), {"document_id": str(document_id)})
    handler(Mock(spec=Session), {"document_id": str(document_id)})

    assert not layout.path("quarantine", document_id).exists()
    assert layout.path("tombstones", document_id, ".deleted").is_file()
    with pytest.raises(JobQueueError, match="lab_cleanup_payload_invalid"):
        handler(Mock(spec=Session), {"document_id": "not-a-uuid"})


def test_durable_cleanup_failure_is_reduced_to_safe_retry_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    handler = make_document_cleanup_handler(DocumentLayout(tmp_path / "uploads"))

    def fail(_layout: DocumentLayout, _document_id: uuid.UUID) -> None:
        raise OSError("synthetic private path detail")

    monkeypatch.setattr("healthcurve.labs.cleanup_jobs.mark_deleted", fail)
    with pytest.raises(JobQueueError, match=r"^lab_document_cleanup_failed$") as error:
        handler(Mock(spec=Session), {"document_id": str(uuid.uuid4())})
    assert "private path" not in str(error.value)
