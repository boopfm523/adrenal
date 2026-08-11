"""Retrieve the newest encrypted set and execute a disposable restore drill."""

from __future__ import annotations

import argparse
import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path, PurePosixPath
from typing import Any, Final

from healthcurve.operations.backup import BackupError, verify_encrypted_set
from healthcurve.operations.rclone_drive import SIDECAR_SUFFIX

if __package__:
    from scripts.check_rclone_drive_config import check_config
else:
    from check_rclone_drive_config import check_config

ENVELOPE_NAME: Final = re.compile(r"^(hc-\d{8}T\d{6}Z-[0-9a-f]{8})\.json$")
REMOTE_NAME: Final = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
SAFE_REASON_CODE: Final = re.compile(r"^restore_[a-z0-9_]{1,80}$")
Runner = Callable[
    [Sequence[str], Mapping[str, str] | None],
    subprocess.CompletedProcess[str],
]


def _production_runner(
    args: Sequence[str], env: Mapping[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # noqa: S603 - resolved tools and argv only
        list(args), check=False, capture_output=True, text=True, env=dict(env) if env else None
    )


def _run(
    runner: Runner,
    args: Sequence[str],
    reason_code: str,
    *,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(args, env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupError(reason_code) from exc
    if result.returncode != 0:
        raise BackupError(reason_code)
    return result


def _destination(value: str) -> tuple[str, str]:
    if ":" not in value:
        raise BackupError("restore_remote_invalid")
    remote, path = value.split(":", 1)
    parsed = PurePosixPath(path)
    if (
        REMOTE_NAME.fullmatch(remote) is None
        or not path
        or path.startswith("/")
        or ".." in parsed.parts
        or any(ord(character) < 32 or ord(character) == 127 for character in path)
    ):
        raise BackupError("restore_remote_invalid")
    return remote, path.rstrip("/")


def _private_file(path: Path, reason: str) -> None:
    if not path.is_absolute():
        raise BackupError(f"{reason}_path_not_absolute")
    try:
        metadata = path.lstat()
    except OSError as exc:
        raise BackupError(f"{reason}_unavailable") from exc
    if (
        stat.S_ISLNK(metadata.st_mode)
        or not stat.S_ISREG(metadata.st_mode)
        or metadata.st_size == 0
    ):
        raise BackupError(f"{reason}_invalid")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        raise BackupError(f"{reason}_permissions_not_owner_only")


def _rclone_base(rclone: str, config: Path) -> tuple[str, ...]:
    return (rclone, "--config", str(config), "--log-level", "ERROR")


def _remote_file(destination: str, name: str) -> str:
    if Path(name).name != name or "/" in name or "\x00" in name:
        raise BackupError("restore_remote_object_invalid")
    return f"{destination}/{name}"


def _copy_remote(
    runner: Runner,
    base: Sequence[str],
    remote: str,
    local: Path,
) -> None:
    _run(
        runner,
        (*base, "copyto", remote, str(local), "--immutable", "--no-traverse"),
        "restore_remote_download_failed",
    )
    try:
        local.chmod(0o600)
    except OSError as exc:
        raise BackupError("restore_download_permissions_failed") from exc


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as exc:
        raise BackupError("restore_download_unreadable") from exc
    return digest.hexdigest()


def _verify_sidecar(path: Path, source: Path) -> None:
    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
        expected = value["sha256"]
    except (OSError, KeyError, TypeError, json.JSONDecodeError) as exc:
        raise BackupError("restore_remote_sidecar_invalid") from exc
    if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise BackupError("restore_remote_sidecar_invalid")
    if _sha256(source) != expected:
        raise BackupError("restore_remote_sidecar_mismatch")


def _newest_envelope(
    runner: Runner,
    base: Sequence[str],
    destination: str,
) -> str:
    result = _run(
        runner,
        (*base, "lsjson", destination, "--files-only", "--no-modtime", "--no-mimetype"),
        "restore_remote_list_failed",
    )
    try:
        entries: Any = json.loads(result.stdout)
    except json.JSONDecodeError as exc:
        raise BackupError("restore_remote_listing_invalid") from exc
    if not isinstance(entries, list):
        raise BackupError("restore_remote_listing_invalid")
    names: list[str] = []
    for entry in entries:
        if not isinstance(entry, dict) or not isinstance(entry.get("Name"), str):
            raise BackupError("restore_remote_listing_invalid")
        name = entry["Name"]
        if ENVELOPE_NAME.fullmatch(name):
            names.append(name)
    if not names:
        raise BackupError("restore_remote_backup_missing")
    return max(names)


def _retrieve_latest(
    config: Path,
    destination: str,
    input_directory: Path,
    *,
    runner: Runner,
    rclone: str,
) -> tuple[Path, datetime]:
    base = _rclone_base(rclone, config)
    envelope_name = _newest_envelope(runner, base, destination)
    envelope = input_directory / envelope_name
    envelope_sidecar = input_directory / f"{envelope_name}{SIDECAR_SUFFIX}"
    _copy_remote(runner, base, _remote_file(destination, envelope_name), envelope)
    _copy_remote(
        runner,
        base,
        _remote_file(destination, f"{envelope_name}{SIDECAR_SUFFIX}"),
        envelope_sidecar,
    )
    _verify_sidecar(envelope_sidecar, envelope)
    try:
        envelope_data: Any = json.loads(envelope.read_text(encoding="utf-8"))
        archive_name = envelope_data["archive"]
        created_at = datetime.fromisoformat(str(envelope_data["created_at"]))
    except (OSError, KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
        raise BackupError("restore_remote_envelope_invalid") from exc
    match = ENVELOPE_NAME.fullmatch(envelope_name)
    if (
        match is None
        or not isinstance(archive_name, str)
        or archive_name != f"{match.group(1)}.tar.age"
        or created_at.tzinfo is None
        or created_at.utcoffset() is None
    ):
        raise BackupError("restore_remote_envelope_invalid")
    archive = input_directory / archive_name
    archive_sidecar = input_directory / f"{archive_name}{SIDECAR_SUFFIX}"
    _copy_remote(runner, base, _remote_file(destination, archive_name), archive)
    _copy_remote(
        runner,
        base,
        _remote_file(destination, f"{archive_name}{SIDECAR_SUFFIX}"),
        archive_sidecar,
    )
    _verify_sidecar(archive_sidecar, archive)
    verify_encrypted_set(envelope)
    return envelope, created_at


def _compose_base(docker: str, project: str, compose_file: Path) -> tuple[str, ...]:
    return (docker, "compose", "--project-name", project, "--file", str(compose_file))


def _result(stdout: str) -> Mapping[str, Any]:
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    try:
        value: Any = json.loads(lines[-1])
    except (IndexError, json.JSONDecodeError) as exc:
        raise BackupError("restore_runner_output_invalid") from exc
    if not isinstance(value, dict) or value.get("status") != "verified":
        raise BackupError("restore_runner_failed")
    expected_true = (
        "alembic_head_verified",
        "restore_sentinel_verified",
        "artifact_canary_verified",
        "ai_write_denied",
        "api_smoke_verified",
    )
    if any(value.get(field) is not True for field in expected_true):
        raise BackupError("restore_runner_evidence_incomplete")
    return value


def _run_restore_runner(
    runner: Runner,
    args: Sequence[str],
    env: Mapping[str, str],
) -> subprocess.CompletedProcess[str]:
    try:
        result = runner(args, env)
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupError("restore_runner_failed") from exc
    if result.returncode == 0:
        return result
    lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
    try:
        value: Any = json.loads(lines[-1])
        reason = value["reason_code"]
    except (IndexError, KeyError, TypeError, json.JSONDecodeError):
        raise BackupError("restore_runner_failed") from None
    if not isinstance(reason, str) or SAFE_REASON_CODE.fullmatch(reason) is None:
        raise BackupError("restore_runner_failed")
    raise BackupError(reason)


def _teardown(
    runner: Runner,
    compose: Sequence[str],
    env: Mapping[str, str],
) -> bool:
    down = (*compose, "down", "--volumes", "--remove-orphans", "--timeout", "10")
    try:
        first = runner(down, env)
        if first.returncode != 0:
            runner((*compose, "kill"), env)
            second = runner(down, env)
            if second.returncode != 0:
                return False
        remaining = runner((*compose, "ps", "--all", "--quiet"), env)
    except (OSError, subprocess.SubprocessError):
        return False
    return remaining.returncode == 0 and not remaining.stdout.strip()


def run_restore_drill(
    *,
    config: Path,
    destination: str,
    identity: Path,
    compose_file: Path,
    runner: Runner = _production_runner,
    rclone: str | None = None,
    docker: str | None = None,
    now: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> Mapping[str, Any]:
    failures = check_config(config)
    if failures:
        raise BackupError("restore_oauth_config_invalid")
    _private_file(identity, "restore_identity")
    _destination(destination)
    if not compose_file.is_absolute() or not compose_file.is_file():
        raise BackupError("restore_compose_file_unavailable")
    resolved_rclone = rclone or shutil.which("rclone")
    resolved_docker = docker or shutil.which("docker")
    if resolved_rclone is None:
        raise BackupError("restore_tool_missing_rclone")
    if resolved_docker is None:
        raise BackupError("restore_tool_missing_docker")

    started_at = now().astimezone(UTC)
    monotonic_start = time.monotonic()
    project = f"hc-restore-{uuid.uuid4().hex[:12]}"
    with tempfile.TemporaryDirectory(prefix="hc-restore-input-") as directory:
        input_directory = Path(directory)
        input_directory.chmod(0o700)
        envelope, created_at = _retrieve_latest(
            config,
            destination,
            input_directory,
            runner=runner,
            rclone=resolved_rclone,
        )
        ephemeral = {
            "HC_RESTORE_POSTGRES_PASSWORD": secrets.token_urlsafe(36),
            "HC_RESTORE_AI_PASSWORD": secrets.token_urlsafe(36),
            "HC_RESTORE_BACKUP_PASSWORD": secrets.token_urlsafe(36),
            "HC_RESTORE_DRILL_INPUT_DIR": str(input_directory),
            "HC_RESTORE_DRILL_ENVELOPE_NAME": envelope.name,
            "HC_RESTORE_DRILL_IDENTITY_FILE": str(identity),
        }
        compose_env = {**os.environ, **ephemeral}
        compose = _compose_base(resolved_docker, project, compose_file)
        teardown_error = False
        failure: BackupError | None = None
        evidence: Mapping[str, Any] = {}
        try:
            try:
                _run(
                    runner,
                    (*compose, "build", "restore-runner"),
                    "restore_image_build_failed",
                    env=compose_env,
                )
                _run(
                    runner,
                    (*compose, "up", "--detach", "--wait", "restore-postgres"),
                    "restore_database_start_failed",
                    env=compose_env,
                )
                completed = _run_restore_runner(
                    runner,
                    (*compose, "run", "--rm", "restore-runner"),
                    compose_env,
                )
                evidence = _result(completed.stdout)
            except BackupError as exc:
                failure = exc
        finally:
            teardown_error = not _teardown(runner, compose, compose_env)
        if teardown_error:
            raise BackupError("restore_teardown_failed")
        if failure is not None:
            raise failure

    elapsed_seconds = time.monotonic() - monotonic_start
    backup_age_hours = max(
        0.0,
        (started_at - created_at.astimezone(UTC)).total_seconds() / 3600,
    )
    return {
        **evidence,
        "backup_age_hours": round(backup_age_hours, 3),
        "elapsed_seconds": round(elapsed_seconds, 3),
        "rpo_met": backup_age_hours <= 24,
        "rto_met": elapsed_seconds <= 4 * 60 * 60,
        "teardown_verified": True,
    }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Restore the newest HealthCurve offsite backup in a disposable stack."
    )
    parser.add_argument("--config", required=True, type=Path)
    identity = parser.add_mutually_exclusive_group(required=True)
    identity.add_argument("--identity", type=Path)
    identity.add_argument(
        "--prompt-identity",
        action="store_true",
        help="paste the age identity at a hidden prompt; keep no persistent key file",
    )
    parser.add_argument(
        "--destination",
        default="healthcurve-drive:HealthCurve Backups",
    )
    parser.add_argument(
        "--compose-file",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "deploy" / "restore-drill.compose.yml",
    )
    args = parser.parse_args(argv)
    try:
        if args.prompt_identity:
            try:
                secret = getpass.getpass("Paste the age recovery identity (input hidden): ")
            except (EOFError, KeyboardInterrupt) as exc:
                raise BackupError("restore_identity_prompt_failed") from exc
            valid_secret = (
                secret.startswith("AGE-SECRET-KEY-") and "\n" not in secret and "\r" not in secret
            )
            if not valid_secret:
                del secret
                raise BackupError("restore_identity_prompt_invalid")
            with tempfile.TemporaryDirectory(prefix="hc-restore-identity-") as directory:
                root = Path(directory)
                root.chmod(0o700)
                identity_path = root / "identity.txt"
                identity_path.write_text(secret + "\n", encoding="utf-8")
                identity_path.chmod(0o600)
                del secret
                result = run_restore_drill(
                    config=args.config,
                    destination=args.destination,
                    identity=identity_path,
                    compose_file=args.compose_file.resolve(),
                )
        else:
            assert args.identity is not None
            result = run_restore_drill(
                config=args.config,
                destination=args.destination,
                identity=args.identity.resolve(),
                compose_file=args.compose_file.resolve(),
            )
    except BackupError as exc:
        print(f"restore drill failure: {exc.reason_code}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
