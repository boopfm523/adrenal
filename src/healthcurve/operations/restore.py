"""Decrypt and validate one HealthCurve backup without exposing restored content.

This module deliberately stops before connecting to PostgreSQL.  It establishes the
cryptographic and archive boundary shared by the isolated restore runner and tests:
the public envelope, separately held age identity, tar structure, manifest, and every
component must agree before a database process can read the dump.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
from collections.abc import Callable, Generator, Mapping, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

from healthcurve.operations.backup import (
    REQUIRED_SCHEMAS,
    BackupError,
    BackupResult,
    verify_encrypted_set,
)

SET_ID: Final = re.compile(r"^hc-\d{8}T\d{6}Z-[0-9a-f]{8}$")
SHA256: Final = re.compile(r"^[0-9a-f]{64}$")
MAX_ARCHIVE_MEMBERS: Final = 100_000
REQUIRED_RESTORE_FILES: Final = frozenset(
    {
        "restore-config/alembic-ini",
        "restore-config/caddyfile",
        "restore-config/docker-compose-yml",
        "restore-config/restore-canary-json",
    }
)

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


@dataclass(frozen=True)
class RestoreTools:
    age: str
    age_keygen: str

    @classmethod
    def resolve(cls) -> RestoreTools:
        age = shutil.which("age")
        age_keygen = shutil.which("age-keygen")
        if age is None:
            raise BackupError("restore_tool_missing_age")
        if age_keygen is None:
            raise BackupError("restore_tool_missing_age_keygen")
        return cls(age=age, age_keygen=age_keygen)


@dataclass(frozen=True)
class ValidatedRestorePayload:
    set_id: str
    created_at: datetime
    root: Path
    database_dump: Path
    uploads: Path
    reports: Path
    restore_canary: Path
    component_count: int


def _production_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - resolved executable and argv only
        list(args), check=False, capture_output=True, text=True
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BackupError("restore_component_unreadable") from exc
    return digest.hexdigest()


def _private_identity(path: Path) -> None:
    if not path.is_absolute():
        raise BackupError("restore_identity_path_not_absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BackupError("restore_identity_unavailable") from exc
    if stat.S_ISLNK(metadata.st_mode):
        raise BackupError("restore_identity_is_symlink")
    if not stat.S_ISREG(metadata.st_mode) or metadata.st_size == 0:
        raise BackupError("restore_identity_invalid")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BackupError("restore_identity_permissions_not_owner_only")


def _run(
    runner: Runner,
    args: Sequence[str],
    reason_code: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(args)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupError(reason_code) from exc
    if result.returncode != 0:
        raise BackupError(reason_code)
    return result


def _safe_relative(value: object, *, reason: str) -> PurePosixPath:
    if not isinstance(value, str) or not value or "\x00" in value:
        raise BackupError(reason)
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or path.as_posix() != value:
        raise BackupError(reason)
    return path


def _validate_archive(archive_path: Path, encrypted_size: int) -> None:
    try:
        with tarfile.open(archive_path, mode="r:") as archive:
            members = archive.getmembers()
    except (OSError, tarfile.TarError) as exc:
        raise BackupError("restore_archive_invalid") from exc
    if not members or len(members) > MAX_ARCHIVE_MEMBERS:
        raise BackupError("restore_archive_invalid")
    names: set[str] = set()
    total_size = 0
    for member in members:
        normalized = member.name.rstrip("/")
        path = _safe_relative(normalized, reason="restore_archive_unsafe")
        if not path.parts or path.parts[0] != "payload" or normalized in names:
            raise BackupError("restore_archive_unsafe")
        names.add(normalized)
        if not (member.isfile() or member.isdir()):
            raise BackupError("restore_archive_unsafe")
        if member.size < 0:
            raise BackupError("restore_archive_invalid")
        if member.isfile():
            total_size += member.size
    if "payload/manifest.json" not in names or total_size > encrypted_size:
        raise BackupError("restore_archive_invalid")


def _extract_archive(archive_path: Path, destination: Path) -> Path:
    try:
        destination.mkdir(mode=0o700)
        with tarfile.open(archive_path, mode="r:") as archive:
            archive.extractall(destination, filter="data")
    except (OSError, tarfile.TarError) as exc:
        raise BackupError("restore_archive_extract_failed") from exc
    payload = destination / "payload"
    if not payload.is_dir() or payload.is_symlink():
        raise BackupError("restore_archive_invalid")
    return payload


def _manifest(payload: Path) -> Mapping[str, Any]:
    path = payload / "manifest.json"
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("restore_manifest_invalid") from exc
    if not isinstance(value, dict):
        raise BackupError("restore_manifest_invalid")
    return value


def _validated_components(
    payload: Path,
    manifest: Mapping[str, Any],
) -> dict[str, Path]:
    components = manifest.get("components")
    if not isinstance(components, list) or not components:
        raise BackupError("restore_manifest_invalid")
    validated: dict[str, Path] = {}
    for component in components:
        if not isinstance(component, dict):
            raise BackupError("restore_manifest_invalid")
        relative = _safe_relative(component.get("path"), reason="restore_manifest_invalid")
        relative_string = relative.as_posix()
        if relative_string == "manifest.json" or relative_string in validated:
            raise BackupError("restore_manifest_invalid")
        size = component.get("size")
        checksum = component.get("sha256")
        if (
            not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(checksum, str)
            or SHA256.fullmatch(checksum) is None
        ):
            raise BackupError("restore_manifest_invalid")
        path = payload.joinpath(*relative.parts)
        try:
            metadata = path.lstat()
        except OSError as exc:
            raise BackupError("restore_component_missing") from exc
        if not stat.S_ISREG(metadata.st_mode):
            raise BackupError("restore_component_invalid")
        if metadata.st_size != size:
            raise BackupError("restore_component_size_mismatch")
        if _sha256(path) != checksum:
            raise BackupError("restore_component_checksum_mismatch")
        validated[relative_string] = path

    actual: set[str] = set()
    try:
        for path in payload.rglob("*"):
            metadata = path.lstat()
            if stat.S_ISLNK(metadata.st_mode) or not (
                stat.S_ISREG(metadata.st_mode) or stat.S_ISDIR(metadata.st_mode)
            ):
                raise BackupError("restore_component_invalid")
            if stat.S_ISREG(metadata.st_mode):
                actual.add(path.relative_to(payload).as_posix())
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError("restore_component_unreadable") from exc
    if actual != {*validated, "manifest.json"}:
        raise BackupError("restore_manifest_file_set_mismatch")
    return validated


def _validate_manifest(
    payload: Path,
    envelope: BackupResult,
    recipient_fingerprint: str,
) -> ValidatedRestorePayload:
    manifest = _manifest(payload)
    try:
        format_version = manifest["format_version"]
        set_id = manifest["set_id"]
        created_at = datetime.fromisoformat(str(manifest["created_at"]))
        required_schemas = manifest["required_schemas"]
        fingerprint = manifest["recipient_fingerprint"]
    except (KeyError, TypeError, ValueError) as exc:
        raise BackupError("restore_manifest_invalid") from exc
    if (
        format_version != 1
        or not isinstance(set_id, str)
        or SET_ID.fullmatch(set_id) is None
        or set_id != envelope.set_id
        or created_at.tzinfo is None
        or created_at.utcoffset() is None
        or required_schemas != list(REQUIRED_SCHEMAS)
        or fingerprint != recipient_fingerprint
    ):
        raise BackupError("restore_manifest_mismatch")
    components = _validated_components(payload, manifest)
    required = {"database.dump", "database.inventory", *REQUIRED_RESTORE_FILES}
    if not required.issubset(components):
        raise BackupError("restore_manifest_required_component_missing")
    uploads = payload / "artifacts" / "uploads"
    reports = payload / "artifacts" / "reports"
    if not uploads.is_dir() or uploads.is_symlink() or not reports.is_dir() or reports.is_symlink():
        raise BackupError("restore_artifact_root_missing")
    return ValidatedRestorePayload(
        set_id=set_id,
        created_at=created_at,
        root=payload,
        database_dump=components["database.dump"],
        uploads=uploads,
        reports=reports,
        restore_canary=components["restore-config/restore-canary-json"],
        component_count=len(components),
    )


@contextmanager
def validated_restore_payload(
    envelope_path: Path,
    identity_path: Path,
    work_root: Path,
    *,
    tools: RestoreTools | None = None,
    runner: Runner = _production_runner,
) -> Generator[ValidatedRestorePayload]:
    """Yield verified plaintext, then remove it on every exit path."""
    _private_identity(identity_path)
    resolved = tools or RestoreTools.resolve()
    envelope = verify_encrypted_set(envelope_path)
    try:
        envelope_data = json.loads(envelope.envelope.read_text(encoding="utf-8"))
        expected_fingerprint = str(envelope_data["recipient_fingerprint"])
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BackupError("restore_envelope_invalid") from exc
    recipient = _run(
        runner,
        (resolved.age_keygen, "-y", str(identity_path)),
        "restore_identity_read_failed",
    ).stdout.strip()
    fingerprint = hashlib.sha256(recipient.encode()).hexdigest()[:16]
    if not recipient.startswith("age1") or fingerprint != expected_fingerprint:
        raise BackupError("restore_identity_fingerprint_mismatch")

    try:
        work_root.mkdir(parents=True, exist_ok=True)
        staging = Path(tempfile.mkdtemp(prefix="hc-restore-", dir=work_root))
        staging.chmod(0o700)
    except OSError as exc:
        raise BackupError("restore_workspace_unavailable") from exc
    try:
        decrypted = staging / "payload.tar"
        _run(
            runner,
            (
                resolved.age,
                "--decrypt",
                "--identity",
                str(identity_path),
                "--output",
                str(decrypted),
                str(envelope.archive),
            ),
            "restore_decryption_failed",
        )
        if not decrypted.is_file() or decrypted.stat().st_size == 0:
            raise BackupError("restore_decrypted_archive_empty")
        decrypted.chmod(0o600)
        _validate_archive(decrypted, envelope.size)
        payload = _extract_archive(decrypted, staging / "extracted")
        yield _validate_manifest(payload, envelope, fingerprint)
    finally:
        shutil.rmtree(staging, ignore_errors=True)
