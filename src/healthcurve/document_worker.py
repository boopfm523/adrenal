"""Network-isolated hostile-PDF structural validation worker (ADR-0010).

The worker deliberately has no database or HTTP client. Its Compose service has
``network_mode: none`` and exchanges only opaque IDs plus stable reason codes with the
API through the private uploads mount.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Final, Protocol

from healthcurve.labs.documents import (
    MAX_PDF_PAGES,
    MAX_VALIDATION_JSON_BYTES,
    DocumentLayout,
    ValidationResult,
    is_deleted,
    write_validation_result,
)

QPDF_TIMEOUT_SECONDS: Final = 20.0
_UNSAFE_PDF_KEYS: Final = frozenset(
    {
        "/AA",
        "/AcroForm",
        "/EmbeddedFiles",
        "/ImportData",
        "/JavaScript",
        "/JS",
        "/Launch",
        "/OpenAction",
        "/RichMedia",
        "/SubmitForm",
        "/XFA",
    }
)


class CommandRunner(Protocol):
    def __call__(
        self, args: list[str], *, timeout: float, stdout_path: Path | None = None
    ) -> CommandResult: ...


@dataclass(frozen=True)
class CommandResult:
    returncode: int
    stdout: str


def _run(args: list[str], *, timeout: float, stdout_path: Path | None = None) -> CommandResult:
    environment = {"PATH": os.environ.get("PATH", "/usr/bin:/bin"), "LANG": "C.UTF-8"}
    if stdout_path is not None:
        descriptor = os.open(stdout_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(descriptor, "w") as output:
            completed = subprocess.run(  # noqa: S603 -- fixed internal argv, no shell
                args,
                check=False,
                stdout=output,
                stderr=subprocess.DEVNULL,
                text=True,
                timeout=timeout,
                shell=False,
                env=environment,
            )
        return CommandResult(completed.returncode, "")
    completed = subprocess.run(  # noqa: S603 -- fixed internal argv, no shell
        args,
        check=False,
        capture_output=True,
        text=True,
        timeout=timeout,
        shell=False,
        env=environment,
    )
    return CommandResult(completed.returncode, completed.stdout)


def validate_one(
    layout: DocumentLayout,
    document_id: uuid.UUID,
    *,
    runner: CommandRunner = _run,
) -> ValidationResult:
    """Claim, inspect, and publish one PDF without exposing its contents in errors."""
    layout.prepare()
    quarantine = layout.path("quarantine", document_id)
    working = layout.path("work", document_id)
    stored = layout.path("stored", document_id)
    if is_deleted(layout, document_id):
        quarantine.unlink(missing_ok=True)
        working.unlink(missing_ok=True)
        raise FileNotFoundError(document_id)
    if quarantine.exists():
        try:
            os.replace(quarantine, working)
        except FileNotFoundError:
            pass
    if not working.exists() and stored.exists():
        # Recover a crash after the validated source was moved but before its small
        # result marker was published. Revalidation is deterministic and fail-closed.
        try:
            os.replace(stored, working)
        except FileNotFoundError:
            pass
    if not working.exists():
        raise FileNotFoundError(document_id)

    sha256 = _sha256(working)
    result: ValidationResult
    try:
        check = runner(["qpdf", "--check", str(working)], timeout=QPDF_TIMEOUT_SECONDS)
        if check.returncode != 0:
            raise PdfRejected("pdf_structure_invalid")

        pages = runner(["qpdf", "--show-npages", str(working)], timeout=QPDF_TIMEOUT_SECONDS)
        if pages.returncode != 0:
            raise PdfRejected("pdf_page_count_invalid")
        try:
            page_count = int(pages.stdout.strip())
        except ValueError as exc:
            raise PdfRejected("pdf_page_count_invalid") from exc
        if not 1 <= page_count <= MAX_PDF_PAGES:
            raise PdfRejected("pdf_page_limit_exceeded")

        with tempfile.TemporaryDirectory(prefix="hc-pdf-") as scratch:
            inspection = Path(scratch) / "inspection.json"
            inspected = runner(
                ["qpdf", "--json-output=2", str(working)],
                timeout=QPDF_TIMEOUT_SECONDS,
                stdout_path=inspection,
            )
            if inspected.returncode != 0 or not inspection.is_file():
                raise PdfRejected("pdf_inspection_failed")
            if inspection.stat().st_size > MAX_VALIDATION_JSON_BYTES:
                raise PdfRejected("pdf_inspection_limit_exceeded")
            try:
                payload = json.loads(inspection.read_bytes())
            except (OSError, json.JSONDecodeError) as exc:
                raise PdfRejected("pdf_inspection_failed") from exc
        if _contains_unsafe_content(payload):
            raise PdfRejected("pdf_interactive_content_rejected")
        if is_deleted(layout, document_id):
            raise FileNotFoundError(document_id)
        os.replace(working, stored)
        result = ValidationResult(
            document_id=document_id,
            sha256=sha256,
            status="stored",
            page_count=page_count,
        )
    except FileNotFoundError:
        working.unlink(missing_ok=True)
        stored.unlink(missing_ok=True)
        raise
    except (PdfRejected, subprocess.TimeoutExpired, OSError) as exc:
        reason = exc.reason_code if isinstance(exc, PdfRejected) else "pdf_validation_failed"
        result = ValidationResult(
            document_id=document_id,
            sha256=sha256,
            status="rejected",
            reason_code=reason,
        )
    if is_deleted(layout, document_id):
        working.unlink(missing_ok=True)
        stored.unlink(missing_ok=True)
        raise FileNotFoundError(document_id)
    write_validation_result(layout, result)
    if result.status == "rejected":
        working.unlink(missing_ok=True)
    return result


class PdfRejected(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


def _sha256(path: Path) -> str:
    import hashlib

    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(64 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _contains_unsafe_content(value: object) -> bool:
    if isinstance(value, dict):
        for key, nested in value.items():
            if key in _UNSAFE_PDF_KEYS:
                return True
            if key == "attachments" and bool(nested):
                return True
            if _contains_unsafe_content(nested):
                return True
    elif isinstance(value, list):
        return any(_contains_unsafe_content(item) for item in value)
    return False


def process_available(
    layout: DocumentLayout,
    *,
    runner: CommandRunner = _run,
) -> int:
    layout.prepare()
    candidates = sorted(
        (*layout.work.glob("*.pdf"), *layout.quarantine.glob("*.pdf"), *layout.stored.glob("*.pdf"))
    )
    processed = 0
    for path in candidates:
        try:
            document_id = uuid.UUID(path.stem)
        except ValueError:
            # Submitted names can never select this path; unknown files are ignored.
            continue
        if layout.path("results", document_id, ".json").exists():
            layout.path("quarantine", document_id).unlink(missing_ok=True)
            layout.path("work", document_id).unlink(missing_ok=True)
            continue
        try:
            validate_one(layout, document_id, runner=runner)
        except FileNotFoundError:
            continue
        processed += 1
    return processed


def main() -> None:
    if shutil.which("qpdf") is None:
        raise SystemExit("qpdf_unavailable")
    root = Path(os.environ.get("HC_UPLOADS_DIR", "/data/uploads"))
    interval = float(os.environ.get("HC_DOCUMENT_POLL_INTERVAL_S", "2"))
    if interval <= 0 or interval > 60:
        raise SystemExit("document_poll_interval_invalid")
    layout = DocumentLayout(root)
    while True:
        process_available(layout)
        time.sleep(interval)


if __name__ == "__main__":
    main()
