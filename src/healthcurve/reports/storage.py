"""Private, checksummed storage for rendered report artifacts."""

from __future__ import annotations

import hashlib
import hmac
import os
import shutil
import uuid
from pathlib import Path
from typing import Final

from sqlalchemy.orm import Session

from healthcurve.reports.models import ReportArtifact, ReportSnapshot
from healthcurve.reports.rendering import RenderedReport

_MEDIA_TYPES: Final = {
    "pdf": "application/pdf",
    "csv": "text/csv; charset=utf-8",
    "json": "application/json",
}


class ArtifactStorageError(RuntimeError):
    pass


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


def _write_once(path: Path, payload: bytes) -> None:
    if path.exists():
        raise ArtifactStorageError("report artifact path already exists")
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    descriptor = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(payload)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


def store(
    session: Session,
    *,
    root: Path,
    snapshot: ReportSnapshot,
    rendered: RenderedReport,
    companion_formats: set[str],
) -> list[ReportArtifact]:
    formats = {"pdf", *companion_formats}
    if not formats <= _MEDIA_TYPES.keys():
        raise ArtifactStorageError("unsupported report artifact format")
    root = root.resolve()
    directory = root / str(snapshot.owner_id) / str(snapshot.id)
    _secure_directory(directory)
    payloads = {"pdf": rendered.pdf, "csv": rendered.csv, "json": rendered.json}
    artifacts: list[ReportArtifact] = []
    written: list[Path] = []
    try:
        for format_name in sorted(formats):
            payload = payloads[format_name]
            relative_path = (
                Path(str(snapshot.owner_id)) / str(snapshot.id) / f"report.{format_name}"
            )
            target = root / relative_path
            _write_once(target, payload)
            written.append(target)
            artifact = ReportArtifact(
                snapshot_id=snapshot.id,
                owner_id=snapshot.owner_id,
                format=format_name,
                media_type=_MEDIA_TYPES[format_name],
                relative_path=relative_path.as_posix(),
                sha256=hashlib.sha256(payload).hexdigest(),
                byte_size=len(payload),
            )
            session.add(artifact)
            artifacts.append(artifact)
    except Exception:
        for path in written:
            path.unlink(missing_ok=True)
        raise
    return artifacts


def read(root: Path, artifact: ReportArtifact) -> bytes:
    root = root.resolve()
    path = (root / artifact.relative_path).resolve()
    if not path.is_relative_to(root):
        raise ArtifactStorageError("report artifact path escapes private root")
    try:
        payload = path.read_bytes()
    except OSError as exc:
        raise ArtifactStorageError("report artifact is unavailable") from exc
    actual = hashlib.sha256(payload).hexdigest()
    if len(payload) != artifact.byte_size or not hmac.compare_digest(actual, artifact.sha256):
        raise ArtifactStorageError("report artifact integrity check failed")
    return payload


def delete_owner_artifacts(root: Path, owner_id: uuid.UUID) -> None:
    root = root.resolve()
    owner_directory = (root / str(owner_id)).resolve()
    if not owner_directory.is_relative_to(root) or owner_directory == root:
        raise ArtifactStorageError("owner artifact path escapes private root")
    if owner_directory.exists():
        shutil.rmtree(owner_directory)
