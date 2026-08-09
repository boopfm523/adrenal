"""Encrypted local backup set creation and ciphertext integrity verification.

The backup host needs only an age *recipient* (public key).  Decryption identities are
deliberately outside this module and outside production configuration.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import tarfile
import tempfile
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Final

REQUIRED_SCHEMAS: Final = ("identity", "fact", "plan", "ai", "ops")
SAFE_LABEL: Final = re.compile(r"^[a-z][a-z0-9_-]*$")


class BackupError(RuntimeError):
    """A privacy-safe failure carrying a stable operational reason code only."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True)
class Toolchain:
    pg_dump: str
    pg_restore: str
    age: str

    @classmethod
    def resolve(cls) -> Toolchain:
        paths: dict[str, str] = {}
        for name in ("pg_dump", "pg_restore", "age"):
            path = shutil.which(name)
            if path is None:
                raise BackupError(f"tool_missing_{name}")
            paths[name] = path
        return cls(**paths)


@dataclass(frozen=True)
class BackupConfig:
    destination: Path
    work_root: Path
    recipient: str
    artifact_roots: Mapping[str, Path]
    restore_files: Mapping[str, Path]
    required_schemas: Sequence[str] = REQUIRED_SCHEMAS


@dataclass(frozen=True)
class BackupResult:
    set_id: str
    archive: Path
    envelope: Path
    sha256: str
    size: int


CommandRunner = Callable[[Sequence[str], Mapping[str, str]], subprocess.CompletedProcess[str]]


def _production_runner(
    args: Sequence[str], env: Mapping[str, str]
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - argv only; executable paths resolved with which()
        list(args),
        check=False,
        capture_output=True,
        text=True,
        env=dict(env),
    )


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _run_checked(
    runner: CommandRunner,
    args: Sequence[str],
    env: Mapping[str, str],
    reason_code: str,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(args, env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupError(reason_code) from exc
    if result.returncode != 0:
        # stderr may contain a connection URL or object name; never place it in the
        # exception that reaches logs or monitoring.
        raise BackupError(reason_code)
    return result


def _validate_label(label: str) -> None:
    if SAFE_LABEL.fullmatch(label) is None:
        raise BackupError("unsafe_component_label")


def _copy_tree(source: Path, target: Path) -> None:
    if not source.exists() or not source.is_dir() or source.is_symlink():
        raise BackupError("artifact_source_invalid")
    target.mkdir(parents=True, exist_ok=False)
    try:
        for item in source.rglob("*"):
            relative = item.relative_to(source)
            destination = target / relative
            if item.is_symlink():
                raise BackupError("artifact_symlink_rejected")
            if item.is_dir():
                destination.mkdir(parents=True, exist_ok=True)
            elif item.is_file():
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(item, destination)
            else:
                raise BackupError("artifact_special_file_rejected")
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError("artifact_copy_failed") from exc


def _copy_restore_files(files: Mapping[str, Path], target: Path) -> None:
    target.mkdir(parents=True, exist_ok=False)
    for label, source in files.items():
        _validate_label(label)
        if not source.exists() or not source.is_file() or source.is_symlink():
            raise BackupError("restore_file_invalid")
        try:
            shutil.copyfile(source, target / label)
        except OSError as exc:
            raise BackupError("restore_file_copy_failed") from exc


def _assert_inventory(inventory: str, schemas: Sequence[str]) -> None:
    for schema in schemas:
        pattern = re.compile(rf"\bSCHEMA\b.*\b{re.escape(schema)}\b")
        if not any(pattern.search(line) for line in inventory.splitlines()):
            raise BackupError("database_inventory_incomplete")


def _component_manifest(payload: Path) -> list[dict[str, Any]]:
    components: list[dict[str, Any]] = []
    for path in sorted(payload.rglob("*")):
        if path.is_file() and path.name != "manifest.json":
            components.append(
                {
                    "path": path.relative_to(payload).as_posix(),
                    "size": path.stat().st_size,
                    "sha256": _sha256(path),
                }
            )
    return components


def _write_json_atomic(path: Path, data: Mapping[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.{uuid.uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            json.dump(data, stream, indent=2, sort_keys=True)
            stream.write("\n")
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise BackupError("manifest_write_failed") from exc


def create_backup(
    config: BackupConfig,
    *,
    tools: Toolchain,
    runner: CommandRunner = _production_runner,
    command_env: Mapping[str, str] | None = None,
    now: datetime | None = None,
) -> BackupResult:
    """Create one finalized encrypted set, removing plaintext on every exit path."""
    if not config.recipient.strip():
        raise BackupError("recipient_missing")
    for label in (*config.artifact_roots.keys(), *config.restore_files.keys()):
        _validate_label(label)

    created_at = now or datetime.now(UTC)
    if created_at.tzinfo is None:
        raise BackupError("backup_time_naive")
    set_id = f"hc-{created_at.astimezone(UTC):%Y%m%dT%H%M%SZ}-{uuid.uuid4().hex[:8]}"

    try:
        config.destination.mkdir(parents=True, exist_ok=True)
        config.work_root.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        raise BackupError("backup_directory_unavailable") from exc

    staging = Path(tempfile.mkdtemp(prefix=f".{set_id}-", dir=config.work_root))
    os.chmod(staging, 0o700)
    partial = config.destination / f".{set_id}.tar.age.partial"
    final = config.destination / f"{set_id}.tar.age"
    envelope = config.destination / f"{set_id}.json"
    env = dict(command_env or os.environ)

    try:
        payload = staging / "payload"
        payload.mkdir(mode=0o700)
        database_dump = payload / "database.dump"
        dump = _run_checked(
            runner,
            (tools.pg_dump, "--format=custom", f"--file={database_dump}"),
            env,
            "database_dump_failed",
        )
        del dump
        if not database_dump.is_file() or database_dump.stat().st_size == 0:
            raise BackupError("database_dump_empty")

        inventory_result = _run_checked(
            runner,
            (tools.pg_restore, "--list", str(database_dump)),
            env,
            "database_inventory_failed",
        )
        _assert_inventory(inventory_result.stdout, config.required_schemas)
        try:
            (payload / "database.inventory").write_text(inventory_result.stdout, encoding="utf-8")
        except OSError as exc:
            raise BackupError("database_inventory_write_failed") from exc

        artifacts = payload / "artifacts"
        artifacts.mkdir()
        for label, source in config.artifact_roots.items():
            _copy_tree(source, artifacts / label)
        _copy_restore_files(config.restore_files, payload / "restore-config")

        manifest = {
            "format_version": 1,
            "set_id": set_id,
            "created_at": created_at.astimezone(UTC).isoformat(),
            "required_schemas": list(config.required_schemas),
            "recipient_fingerprint": hashlib.sha256(config.recipient.encode()).hexdigest()[:16],
            "components": _component_manifest(payload),
        }
        _write_json_atomic(payload / "manifest.json", manifest)

        tar_path = staging / f"{set_id}.tar"
        try:
            with tarfile.open(tar_path, mode="w") as archive:
                archive.add(payload, arcname="payload", recursive=True)
        except (OSError, tarfile.TarError) as exc:
            raise BackupError("archive_create_failed") from exc

        _run_checked(
            runner,
            (
                tools.age,
                "--encrypt",
                "--recipient",
                config.recipient,
                "--output",
                str(partial),
                str(tar_path),
            ),
            env,
            "encryption_failed",
        )
        if not partial.is_file() or partial.stat().st_size == 0:
            raise BackupError("encrypted_archive_empty")

        try:
            os.replace(partial, final)
            ciphertext_sha256 = _sha256(final)
            size = final.stat().st_size
        except OSError as exc:
            final.unlink(missing_ok=True)
            raise BackupError("encrypted_archive_finalize_failed") from exc
        public_envelope = {
            "format_version": 1,
            "set_id": set_id,
            "created_at": created_at.astimezone(UTC).isoformat(),
            "archive": final.name,
            "size": size,
            "sha256": ciphertext_sha256,
            "recipient_fingerprint": manifest["recipient_fingerprint"],
            "verified": True,
        }
        try:
            _write_json_atomic(envelope, public_envelope)
        except BackupError:
            final.unlink(missing_ok=True)
            raise
        return BackupResult(set_id, final, envelope, ciphertext_sha256, size)
    finally:
        partial.unlink(missing_ok=True)
        shutil.rmtree(staging, ignore_errors=True)


def verify_encrypted_set(envelope_path: Path) -> BackupResult:
    """Verify the public envelope and finalized ciphertext without decrypting it."""
    try:
        data = json.loads(envelope_path.read_text(encoding="utf-8"))
        archive_name = data["archive"]
        if not isinstance(archive_name, str) or Path(archive_name).name != archive_name:
            raise BackupError("envelope_invalid")
        archive = envelope_path.parent / archive_name
        expected_size = int(data["size"])
        expected_sha256 = str(data["sha256"])
        set_id = str(data["set_id"])
    except BackupError:
        raise
    except (OSError, ValueError, TypeError, KeyError, json.JSONDecodeError) as exc:
        raise BackupError("envelope_invalid") from exc
    try:
        if not archive.is_file() or archive.stat().st_size != expected_size:
            raise BackupError("ciphertext_size_mismatch")
        actual = _sha256(archive)
    except BackupError:
        raise
    except OSError as exc:
        raise BackupError("ciphertext_read_failed") from exc
    if actual != expected_sha256:
        raise BackupError("ciphertext_checksum_mismatch")
    return BackupResult(set_id, archive, envelope_path, actual, expected_size)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Create or verify an encrypted backup set")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--destination", type=Path, required=True)
    create.add_argument("--work-root", type=Path, required=True)
    create.add_argument("--recipient", required=True)
    create.add_argument("--uploads", type=Path, required=True)
    create.add_argument("--reports", type=Path, required=True)
    create.add_argument("--restore-file", action="append", default=[])
    verify = subparsers.add_parser("verify")
    verify.add_argument("envelope", type=Path)
    args = parser.parse_args(argv)

    try:
        if args.command == "verify":
            result = verify_encrypted_set(args.envelope)
        else:
            restore_files: dict[str, Path] = {}
            for value in args.restore_file:
                source = Path(value)
                restore_files[source.name.lower().replace(".", "-")] = source
            result = create_backup(
                BackupConfig(
                    destination=args.destination,
                    work_root=args.work_root,
                    recipient=args.recipient,
                    artifact_roots={"uploads": args.uploads, "reports": args.reports},
                    restore_files=restore_files,
                ),
                tools=Toolchain.resolve(),
            )
    except BackupError as exc:
        print(exc.reason_code)
        return 1
    print(json.dumps({"set_id": result.set_id, "sha256": result.sha256, "size": result.size}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
