"""Atomic private storage for streamed complete-owner exports."""

from __future__ import annotations

import hashlib
import hmac
import os
import uuid
from dataclasses import dataclass
from pathlib import Path


class PrivateExportStorageError(RuntimeError):
    """Privacy-safe export artifact failure."""


@dataclass(frozen=True, slots=True)
class StoredExport:
    relative_path: str
    sha256: str
    byte_size: int


def _secure_directory(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    path.chmod(0o700)


class AtomicExportWriter:
    """Hash bytes while writing, then publish once without replacing an artifact."""

    def __init__(self, root: Path, *, owner_id: uuid.UUID, export_id: uuid.UUID) -> None:
        self.root = root.resolve()
        self.relative_path = Path(str(owner_id)) / "exports" / str(export_id) / "export.json"
        self.target = self.root / self.relative_path
        _secure_directory(self.target.parent)
        self.temporary = self.target.with_name(f".{self.target.name}.{uuid.uuid4().hex}.tmp")
        descriptor = os.open(self.temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        self._output = os.fdopen(descriptor, "wb")
        self._digest = hashlib.sha256()
        self._size = 0
        self._finished = False

    def write(self, value: str | bytes) -> None:
        payload = value.encode("utf-8") if isinstance(value, str) else value
        self._output.write(payload)
        self._digest.update(payload)
        self._size += len(payload)

    def finish(self) -> StoredExport:
        if self._finished:
            raise PrivateExportStorageError("export_writer_already_finished")
        self._output.flush()
        os.fsync(self._output.fileno())
        self._output.close()
        try:
            os.link(self.temporary, self.target)
        except FileExistsError:
            existing = inspect(self.root, self.relative_path.as_posix())
            self.temporary.unlink(missing_ok=True)
            self._finished = True
            return existing
        except OSError as exc:
            self.temporary.unlink(missing_ok=True)
            raise PrivateExportStorageError("export_artifact_publish_failed") from exc
        self.temporary.unlink(missing_ok=True)
        self._finished = True
        return StoredExport(self.relative_path.as_posix(), self._digest.hexdigest(), self._size)

    def abort(self) -> None:
        if not self._output.closed:
            self._output.close()
        self.temporary.unlink(missing_ok=True)


def resolve(root: Path, relative_path: str) -> Path:
    resolved_root = root.resolve()
    path = (resolved_root / relative_path).resolve()
    if not path.is_relative_to(resolved_root) or path == resolved_root or path.is_symlink():
        raise PrivateExportStorageError("export_artifact_path_invalid")
    return path


def inspect(root: Path, relative_path: str) -> StoredExport:
    path = resolve(root, relative_path)
    digest = hashlib.sha256()
    size = 0
    try:
        with path.open("rb") as source:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
    except OSError as exc:
        raise PrivateExportStorageError("export_artifact_unavailable") from exc
    if size <= 0:
        raise PrivateExportStorageError("export_artifact_invalid")
    return StoredExport(relative_path, digest.hexdigest(), size)


def verified_path(
    root: Path, *, relative_path: str, expected_sha256: str, expected_size: int
) -> Path:
    actual = inspect(root, relative_path)
    if actual.byte_size != expected_size or not hmac.compare_digest(actual.sha256, expected_sha256):
        raise PrivateExportStorageError("export_artifact_integrity_failed")
    return resolve(root, relative_path)


def available_path(root: Path, *, relative_path: str, expected_size: int) -> Path:
    """Validate containment and immutable-file size without buffering the download."""
    path = resolve(root, relative_path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise PrivateExportStorageError("export_artifact_unavailable") from exc
    if not path.is_file() or stat.st_size != expected_size:
        raise PrivateExportStorageError("export_artifact_integrity_failed")
    return path


def delete(root: Path, relative_path: str) -> None:
    path = resolve(root, relative_path)
    try:
        path.unlink(missing_ok=True)
    except OSError as exc:
        raise PrivateExportStorageError("export_artifact_delete_failed") from exc
    parent = path.parent
    for _ in range(2):
        try:
            parent.rmdir()
        except OSError:
            break
        parent = parent.parent


def remove_stale_temporary_files(root: Path, *, older_than_epoch: float) -> int:
    resolved_root = root.resolve()
    if not resolved_root.exists():
        return 0
    removed = 0
    for path in resolved_root.glob("*/exports/*/.export.json.*.tmp"):
        try:
            if path.is_file() and not path.is_symlink() and path.stat().st_mtime < older_than_epoch:
                path.unlink()
                removed += 1
        except OSError:
            continue
    return removed
