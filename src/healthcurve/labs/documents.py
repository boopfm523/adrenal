"""Private source-document storage and filesystem validation mailbox.

The API only performs bounded streaming and format sniffing. Structural PDF parsing is
performed by ``healthcurve.document_worker`` in a container with no network. The two
processes exchange opaque IDs and privacy-safe result codes through this directory;
medical content and submitted filenames never appear in the mailbox metadata.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO, Final

from pydantic import BaseModel, Field, ValidationError, model_validator

from healthcurve.labs.pdf_schemas import EmbeddedExtractionResult

MAX_PDF_BYTES: Final = 25 * 1024 * 1024
MAX_PDF_PAGES: Final = 100
MAX_VALIDATION_JSON_BYTES: Final = 10 * 1024 * 1024
MAX_EXTRACTION_JSON_BYTES: Final = 8 * 1024 * 1024
MAX_PAGE_PREVIEW_BYTES: Final = 10 * 1024 * 1024
PDF_MEDIA_TYPE: Final = "application/pdf"
_COPY_CHUNK: Final = 64 * 1024
_SAFE_NAME = re.compile(r"[^\w .()\[\]-]+", re.UNICODE)


class DocumentStorageError(RuntimeError):
    """A stable reason code safe to expose without document contents."""


class ValidationResult(BaseModel):
    document_id: uuid.UUID
    sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    status: str = Field(pattern=r"^(stored|rejected)$")
    page_count: int | None = Field(default=None, ge=1, le=MAX_PDF_PAGES)
    reason_code: str | None = Field(default=None, max_length=64)

    @model_validator(mode="after")
    def status_fields_match(self) -> ValidationResult:
        if self.status == "stored" and self.page_count is None:
            raise ValueError("stored validation result requires page_count")
        if self.status == "rejected" and self.reason_code is None:
            raise ValueError("rejected validation result requires reason_code")
        return self


@dataclass(frozen=True)
class StoredUpload:
    document_id: uuid.UUID
    display_name: str
    media_type: str
    sha256: str
    byte_size: int


@dataclass(frozen=True)
class DocumentLayout:
    root: Path

    @property
    def quarantine(self) -> Path:
        return self.root / "quarantine"

    @property
    def work(self) -> Path:
        return self.root / "work"

    @property
    def stored(self) -> Path:
        return self.root / "stored"

    @property
    def results(self) -> Path:
        return self.root / "results"

    @property
    def tombstones(self) -> Path:
        return self.root / "tombstones"

    @property
    def extractions(self) -> Path:
        return self.root / "extractions"

    @property
    def previews(self) -> Path:
        return self.root / "previews"

    def prepare(self) -> None:
        for directory in (
            self.root,
            self.quarantine,
            self.work,
            self.stored,
            self.results,
            self.tombstones,
            self.extractions,
            self.previews,
        ):
            directory.mkdir(mode=0o700, parents=True, exist_ok=True)
            directory.chmod(0o700)

    def path(self, area: str, document_id: uuid.UUID, suffix: str = ".pdf") -> Path:
        if area not in {
            "quarantine",
            "work",
            "stored",
            "results",
            "tombstones",
            "extractions",
            "previews",
        }:
            raise DocumentStorageError("document_storage_area_invalid")
        return getattr(self, area) / f"{document_id}{suffix}"

    def preview_path(self, document_id: uuid.UUID, page_number: int) -> Path:
        if not 1 <= page_number <= MAX_PDF_PAGES:
            raise DocumentStorageError("document_preview_page_invalid")
        return self.previews / f"{document_id}-{page_number}.png"


def store_pdf_upload(
    source: BinaryIO,
    *,
    layout: DocumentLayout,
    submitted_name: str | None,
    media_type: str | None,
    document_id: uuid.UUID | None = None,
) -> StoredUpload:
    """Stream one bounded PDF into quarantine under an opaque generated name."""
    if (media_type or "").casefold() != PDF_MEDIA_TYPE:
        raise DocumentStorageError("pdf_media_type_invalid")
    layout.prepare()
    resolved_id = document_id or uuid.uuid4()
    partial = layout.path("quarantine", resolved_id, ".part")
    target = layout.path("quarantine", resolved_id)
    digest = hashlib.sha256()
    total = 0
    header = b""
    try:
        descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "wb") as destination:
            while True:
                chunk = source.read(_COPY_CHUNK)
                if not chunk:
                    break
                total += len(chunk)
                if total > MAX_PDF_BYTES:
                    raise DocumentStorageError("pdf_size_invalid")
                if len(header) < 8:
                    header += chunk[: 8 - len(header)]
                digest.update(chunk)
                destination.write(chunk)
            destination.flush()
            os.fsync(destination.fileno())
        if total == 0 or not header.startswith(b"%PDF-"):
            raise DocumentStorageError("pdf_signature_invalid")
        os.replace(partial, target)
    except Exception:
        partial.unlink(missing_ok=True)
        target.unlink(missing_ok=True)
        raise
    return StoredUpload(
        document_id=resolved_id,
        display_name=safe_display_name(submitted_name),
        media_type=PDF_MEDIA_TYPE,
        sha256=digest.hexdigest(),
        byte_size=total,
    )


def safe_display_name(submitted_name: str | None) -> str:
    """Keep limited display metadata, never a submitted path or control characters."""
    leaf = Path((submitted_name or "document.pdf").replace("\\", "/")).name
    cleaned = _SAFE_NAME.sub("_", leaf).strip(" .")[:251]
    if not cleaned:
        cleaned = "document"
    if not cleaned.casefold().endswith(".pdf"):
        cleaned += ".pdf"
    return cleaned[:255]


def load_validation_result(
    layout: DocumentLayout, document_id: uuid.UUID
) -> ValidationResult | None:
    path = layout.path("results", document_id, ".json")
    if not path.exists():
        return None
    try:
        if path.stat().st_size > 4096:
            raise DocumentStorageError("document_validation_result_invalid")
        result = ValidationResult.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise DocumentStorageError("document_validation_result_invalid") from exc
    if result.document_id != document_id:
        raise DocumentStorageError("document_validation_result_invalid")
    return result


def write_validation_result(layout: DocumentLayout, result: ValidationResult) -> None:
    layout.prepare()
    target = layout.path("results", result.document_id, ".json")
    partial = layout.results / f".{result.document_id}.{uuid.uuid4()}.part"
    payload = json.dumps(result.model_dump(mode="json"), sort_keys=True).encode()
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def load_extraction_result(
    layout: DocumentLayout, document_id: uuid.UUID
) -> EmbeddedExtractionResult | None:
    path = layout.path("extractions", document_id, ".json")
    if not path.exists():
        return None
    try:
        if path.stat().st_size > MAX_EXTRACTION_JSON_BYTES:
            raise DocumentStorageError("document_extraction_result_invalid")
        result = EmbeddedExtractionResult.model_validate_json(path.read_bytes())
    except (OSError, ValidationError, ValueError) as exc:
        raise DocumentStorageError("document_extraction_result_invalid") from exc
    if result.document_id != document_id:
        raise DocumentStorageError("document_extraction_result_invalid")
    return result


def write_extraction_result(layout: DocumentLayout, result: EmbeddedExtractionResult) -> None:
    layout.prepare()
    target = layout.path("extractions", result.document_id, ".json")
    partial = layout.extractions / f".{result.document_id}.{uuid.uuid4()}.part"
    payload = result.model_dump_json().encode()
    if len(payload) > MAX_EXTRACTION_JSON_BYTES:
        raise DocumentStorageError("document_extraction_result_too_large")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def write_page_preview(
    layout: DocumentLayout, document_id: uuid.UUID, page_number: int, source: Path
) -> None:
    """Publish one bounded inert PNG from the no-network renderer."""
    layout.prepare()
    target = layout.preview_path(document_id, page_number)
    partial = layout.previews / f".{document_id}-{page_number}.{uuid.uuid4()}.part"
    payload = source.read_bytes()
    if not payload.startswith(b"\x89PNG\r\n\x1a\n") or len(payload) > MAX_PAGE_PREVIEW_BYTES:
        raise DocumentStorageError("document_preview_invalid")
    descriptor = os.open(partial, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as destination:
            destination.write(payload)
            destination.flush()
            os.fsync(destination.fileno())
        os.replace(partial, target)
    finally:
        partial.unlink(missing_ok=True)


def mark_deleted(layout: DocumentLayout, document_id: uuid.UUID) -> None:
    """Tombstone before unlinking so an in-flight worker cannot republish the PDF."""
    layout.prepare()
    tombstone = layout.path("tombstones", document_id, ".deleted")
    descriptor = os.open(tombstone, os.O_WRONLY | os.O_CREAT, 0o600)
    os.close(descriptor)
    for area in ("quarantine", "work", "stored"):
        layout.path(area, document_id).unlink(missing_ok=True)
    layout.path("quarantine", document_id, ".part").unlink(missing_ok=True)
    layout.path("results", document_id, ".json").unlink(missing_ok=True)
    layout.path("extractions", document_id, ".json").unlink(missing_ok=True)
    for preview in layout.previews.glob(f"{document_id}-*.png"):
        preview.unlink(missing_ok=True)
    for partial in layout.results.glob(f".{document_id}.*.part"):
        partial.unlink(missing_ok=True)
    for partial in layout.extractions.glob(f".{document_id}.*.part"):
        partial.unlink(missing_ok=True)
    for partial in layout.previews.glob(f".{document_id}-*.part"):
        partial.unlink(missing_ok=True)


def is_deleted(layout: DocumentLayout, document_id: uuid.UUID) -> bool:
    return layout.path("tombstones", document_id, ".deleted").exists()
