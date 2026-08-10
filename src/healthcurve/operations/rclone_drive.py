"""Google Drive offsite writer using a capability-narrow rclone invocation.

Google Drive cannot provide object lock or a truly write-only credential. ADR-0012 is
unrelated; the owner accepted this backup-specific downgrade in hc-cbs.8.4. This
adapter still exposes no delete operation, uses immutable uploads, and verifies a
small SHA-256 sidecar plus remote object size after every upload.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import subprocess
import tempfile
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path, PurePosixPath
from typing import Any, Final

from healthcurve.operations.backup import BackupError
from healthcurve.operations.retention import OffsiteSettings, OffsiteWriter, RemoteObject

PROVIDER: Final = "rclone-google-drive"
REMOTE_KEY: Final = re.compile(r"^[a-z][a-z0-9-]{0,62}:[^\x00-\x1f\x7f]+$")
SIDECAR_SUFFIX: Final = ".hc-sha256.json"

Runner = Callable[[Sequence[str]], subprocess.CompletedProcess[str]]


def _production_runner(args: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - fixed executable and argv, never a shell
        list(args), check=False, capture_output=True, text=True
    )


class RcloneDriveWriter(OffsiteWriter):
    def __init__(
        self,
        *,
        config_file: Path,
        executable: str | None = None,
        runner: Runner = _production_runner,
    ) -> None:
        resolved = executable or shutil.which("rclone")
        if resolved is None:
            raise BackupError("offsite_tool_missing_rclone")
        self._executable = resolved
        self._config_file = config_file
        self._runner = runner

    def head(self, key: str) -> RemoteObject | None:
        remote = self._remote(key)
        stat = self._stat(remote)
        sidecar = self._read_sidecar(remote + SIDECAR_SUFFIX)
        if stat is None:
            # A sidecar-first interrupted upload is intentionally recoverable.
            return None
        if sidecar is None:
            raise BackupError("offsite_metadata_missing")
        return RemoteObject(size=stat, sha256=sidecar)

    def put_if_absent(self, key: str, source: Path, metadata: Mapping[str, str]) -> None:
        remote = self._remote(key)
        expected_sha = metadata.get("sha256", "")
        if not re.fullmatch(r"[0-9a-f]{64}", expected_sha):
            raise BackupError("offsite_metadata_invalid")
        if _sha256(source) != expected_sha:
            raise BackupError("offsite_source_changed")

        existing_sidecar = self._read_sidecar(remote + SIDECAR_SUFFIX)
        if existing_sidecar is not None and existing_sidecar != expected_sha:
            raise BackupError("offsite_object_conflict")
        if existing_sidecar is None:
            with tempfile.TemporaryDirectory(prefix="hc-rclone-sidecar-") as directory:
                sidecar = Path(directory) / "checksum.json"
                sidecar.write_text(
                    json.dumps({"sha256": expected_sha}, sort_keys=True) + "\n",
                    encoding="utf-8",
                )
                sidecar.chmod(0o600)
                self._copy_immutable(sidecar, remote + SIDECAR_SUFFIX)
        try:
            source_size = source.stat().st_size
        except OSError as exc:
            raise BackupError("offsite_source_unavailable") from exc
        existing_size = self._stat(remote)
        if existing_size is not None:
            if existing_size != source_size:
                raise BackupError("offsite_object_conflict")
            return
        self._copy_immutable(source, remote)

    def _remote(self, key: str) -> str:
        remote_path = key.split(":", 1)[1] if ":" in key else ""
        if (
            REMOTE_KEY.fullmatch(key) is None
            or remote_path.startswith("/")
            or ".." in PurePosixPath(remote_path).parts
        ):
            raise BackupError("offsite_destination_invalid")
        return key

    def _base(self) -> list[str]:
        return [
            self._executable,
            "--config",
            str(self._config_file),
            "--log-level",
            "ERROR",
        ]

    def _run(self, args: Sequence[str], reason: str) -> str:
        try:
            result = self._runner([*self._base(), *args])
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackupError(reason) from exc
        if result.returncode != 0:
            raise BackupError(reason)
        return result.stdout

    def _stat(self, remote: str) -> int | None:
        result = self._invoke(("lsjson", remote, "--stat", "--no-modtime"))
        if result.returncode != 0:
            # `lsjson --stat` uses exit status 3 for a missing exact object.
            if result.returncode == 3:
                return None
            raise BackupError("offsite_transport_failed")
        try:
            value: Any = json.loads(result.stdout)
            if not isinstance(value, dict) or value.get("IsDir") is not False:
                raise BackupError("offsite_metadata_invalid")
            size = int(value["Size"])
        except BackupError:
            raise
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise BackupError("offsite_metadata_invalid") from exc
        return size

    def _read_sidecar(self, remote: str) -> str | None:
        result = self._invoke(("cat", remote, "--count", "256"))
        if result.returncode != 0:
            if result.returncode == 3:
                return None
            raise BackupError("offsite_transport_failed")
        try:
            value = json.loads(result.stdout)
            checksum = value["sha256"]
        except (KeyError, TypeError, json.JSONDecodeError) as exc:
            raise BackupError("offsite_metadata_invalid") from exc
        if not isinstance(checksum, str) or re.fullmatch(r"[0-9a-f]{64}", checksum) is None:
            raise BackupError("offsite_metadata_invalid")
        return checksum

    def _copy_immutable(self, source: Path, remote: str) -> None:
        self._run(
            ("copyto", str(source), remote, "--immutable", "--no-traverse"),
            "offsite_transport_failed",
        )

    def _invoke(self, args: Sequence[str]) -> subprocess.CompletedProcess[str]:
        try:
            return self._runner([*self._base(), *args])
        except (OSError, subprocess.SubprocessError) as exc:
            raise BackupError("offsite_transport_failed") from exc


def writer_from_settings(settings: OffsiteSettings) -> OffsiteWriter | None:
    if not settings.enabled:
        return None
    if settings.provider != PROVIDER:
        raise BackupError("offsite_provider_unsupported")
    if settings.credential_file is None:
        raise BackupError("offsite_configuration_incomplete")
    return RcloneDriveWriter(config_file=settings.credential_file)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BackupError("offsite_source_unavailable") from exc
    return digest.hexdigest()
