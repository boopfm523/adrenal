from __future__ import annotations

import hashlib
import json
import subprocess
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
from scripts.run_restore_drill import main, run_restore_drill

from healthcurve.operations.backup import BackupError
from healthcurve.operations.rclone_drive import SIDECAR_SUFFIX

SET_ID = "hc-20260810T210718Z-1234abcd"
DESTINATION = "healthcurve-drive:HealthCurve Backups"


def _sha256(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def _config(path: Path) -> Path:
    path.write_text(
        "\n".join(
            (
                "[healthcurve-drive]",
                "type = drive",
                "client_id = synthetic.apps.googleusercontent.com",
                "client_secret = synthetic-obscured-secret",
                "scope = drive.file",
                'token = {"refresh_token":"synthetic-refresh"}',
                "service_account_file =",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def _inputs(tmp_path: Path) -> tuple[Path, Path, Path, dict[str, bytes], datetime]:
    config = _config(tmp_path / "rclone.conf")
    identity = tmp_path / "identity.txt"
    identity.write_text("AGE-SECRET-KEY-SYNTHETIC\n", encoding="utf-8")
    identity.chmod(0o600)
    compose = tmp_path / "restore.compose.yml"
    compose.write_text("services: {}\n", encoding="utf-8")

    created_at = datetime(2026, 8, 10, 21, 7, 18, tzinfo=UTC)
    archive_name = f"{SET_ID}.tar.age"
    envelope_name = f"{SET_ID}.json"
    archive = b"synthetic encrypted backup"
    envelope = (
        json.dumps(
            {
                "format_version": 1,
                "set_id": SET_ID,
                "created_at": created_at.isoformat(),
                "archive": archive_name,
                "size": len(archive),
                "sha256": _sha256(archive),
                "recipient_fingerprint": "0123456789abcdef",
                "verified": True,
            },
            sort_keys=True,
        )
        + "\n"
    ).encode()
    remote = {
        f"{DESTINATION}/{envelope_name}": envelope,
        f"{DESTINATION}/{envelope_name}{SIDECAR_SUFFIX}": json.dumps(
            {"sha256": _sha256(envelope)}
        ).encode(),
        f"{DESTINATION}/{archive_name}": archive,
        f"{DESTINATION}/{archive_name}{SIDECAR_SUFFIX}": json.dumps(
            {"sha256": _sha256(archive)}
        ).encode(),
    }
    return config, identity, compose, remote, created_at


class FakeCommands:
    def __init__(self, remote: Mapping[str, bytes]) -> None:
        self.remote = dict(remote)
        self.calls: list[tuple[tuple[str, ...], Mapping[str, str] | None]] = []
        self.runner_fails = False
        self.runner_reason: str | None = None
        self.teardown_fails = False
        self.input_directory: Path | None = None

    def __call__(
        self,
        args: Sequence[str],
        env: Mapping[str, str] | None = None,
    ) -> subprocess.CompletedProcess[str]:
        command = tuple(args)
        self.calls.append((command, env))
        if Path(command[0]).name == "rclone":
            if "lsjson" in command:
                listing = [
                    {"Name": key.removeprefix(f"{DESTINATION}/")} for key in sorted(self.remote)
                ]
                return subprocess.CompletedProcess(args, 0, json.dumps(listing), "")
            if "copyto" in command:
                index = command.index("copyto")
                source = command[index + 1]
                target = Path(command[index + 2])
                target.write_bytes(self.remote[source])
                return subprocess.CompletedProcess(args, 0, "", "")
        if "compose" in command:
            assert env is not None
            self.input_directory = Path(env["HC_RESTORE_DRILL_INPUT_DIR"])
            if "down" in command:
                return subprocess.CompletedProcess(args, int(self.teardown_fails), "", "")
            if "run" in command:
                if self.runner_fails:
                    failure_output = (
                        json.dumps({"status": "failed", "reason_code": self.runner_reason})
                        if self.runner_reason is not None
                        else "private restored content"
                    )
                    return subprocess.CompletedProcess(args, 1, failure_output, "")
                output: dict[str, Any] = {
                    "status": "verified",
                    "component_count": 9,
                    "required_schema_count": 5,
                    "alembic_head_verified": True,
                    "restore_sentinel_verified": True,
                    "artifact_canary_verified": True,
                    "ai_write_denied": True,
                    "api_smoke_verified": True,
                }
                return subprocess.CompletedProcess(args, 0, json.dumps(output) + "\n", "")
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected command: {command}")


def test_drill_retrieves_newest_set_runs_isolated_stack_and_tears_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    config, identity, compose, remote, created_at = _inputs(tmp_path)
    fake = FakeCommands(remote)
    ticks = iter((100.0, 112.5))
    monkeypatch.setattr("scripts.run_restore_drill.time.monotonic", lambda: next(ticks))
    result = run_restore_drill(
        config=config,
        destination=DESTINATION,
        identity=identity,
        compose_file=compose,
        runner=fake,
        rclone="/synthetic/rclone",
        docker="/synthetic/docker",
        now=lambda: created_at + timedelta(hours=1),
    )
    assert result["status"] == "verified"
    assert result["backup_age_hours"] == 1.0
    assert result["elapsed_seconds"] == 12.5
    assert result["rpo_met"] is True
    assert result["rto_met"] is True
    assert result["teardown_verified"] is True
    assert any("--project-name" in call and "down" in call for call, _ in fake.calls)
    assert fake.input_directory is not None
    assert not fake.input_directory.exists()


def test_runner_failure_is_redacted_and_still_tears_down(tmp_path: Path) -> None:
    config, identity, compose, remote, created_at = _inputs(tmp_path)
    fake = FakeCommands(remote)
    fake.runner_fails = True
    with pytest.raises(BackupError, match=r"^restore_runner_failed$") as failure:
        run_restore_drill(
            config=config,
            destination=DESTINATION,
            identity=identity,
            compose_file=compose,
            runner=fake,
            rclone="/synthetic/rclone",
            docker="/synthetic/docker",
            now=lambda: created_at,
        )
    assert "private restored content" not in str(failure.value)
    assert any("down" in call for call, _ in fake.calls)
    assert fake.input_directory is not None
    assert not fake.input_directory.exists()


def test_teardown_failure_overrides_runner_failure(tmp_path: Path) -> None:
    config, identity, compose, remote, created_at = _inputs(tmp_path)
    fake = FakeCommands(remote)
    fake.runner_fails = True
    fake.teardown_fails = True
    with pytest.raises(BackupError, match=r"^restore_teardown_failed$"):
        run_restore_drill(
            config=config,
            destination=DESTINATION,
            identity=identity,
            compose_file=compose,
            runner=fake,
            rclone="/synthetic/rclone",
            docker="/synthetic/docker",
            now=lambda: created_at,
        )


def test_safe_runner_reason_is_preserved_without_private_output(tmp_path: Path) -> None:
    config, identity, compose, remote, created_at = _inputs(tmp_path)
    fake = FakeCommands(remote)
    fake.runner_fails = True
    fake.runner_reason = "restore_sentinel_mismatch"
    with pytest.raises(BackupError, match=r"^restore_sentinel_mismatch$"):
        run_restore_drill(
            config=config,
            destination=DESTINATION,
            identity=identity,
            compose_file=compose,
            runner=fake,
            rclone="/synthetic/rclone",
            docker="/synthetic/docker",
            now=lambda: created_at,
        )


def test_unsafe_runner_reason_is_replaced(tmp_path: Path) -> None:
    config, identity, compose, remote, created_at = _inputs(tmp_path)
    fake = FakeCommands(remote)
    fake.runner_fails = True
    fake.runner_reason = "private database output"
    with pytest.raises(BackupError, match=r"^restore_runner_failed$"):
        run_restore_drill(
            config=config,
            destination=DESTINATION,
            identity=identity,
            compose_file=compose,
            runner=fake,
            rclone="/synthetic/rclone",
            docker="/synthetic/docker",
            now=lambda: created_at,
        )


def test_insecure_identity_fails_before_remote_or_docker_commands(tmp_path: Path) -> None:
    config, identity, compose, remote, created_at = _inputs(tmp_path)
    identity.chmod(0o644)
    fake = FakeCommands(remote)
    with pytest.raises(BackupError, match=r"^restore_identity_permissions_not_owner_only$"):
        run_restore_drill(
            config=config,
            destination=DESTINATION,
            identity=identity,
            compose_file=compose,
            runner=fake,
            rclone="/synthetic/rclone",
            docker="/synthetic/docker",
            now=lambda: created_at,
        )
    assert fake.calls == []


def test_remote_sidecar_mismatch_fails_before_docker(tmp_path: Path) -> None:
    config, identity, compose, remote, created_at = _inputs(tmp_path)
    envelope_sidecar = next(key for key in remote if key.endswith(f".json{SIDECAR_SUFFIX}"))
    remote[envelope_sidecar] = json.dumps({"sha256": "0" * 64}).encode()
    fake = FakeCommands(remote)
    with pytest.raises(BackupError, match=r"^restore_remote_sidecar_mismatch$"):
        run_restore_drill(
            config=config,
            destination=DESTINATION,
            identity=identity,
            compose_file=compose,
            runner=fake,
            rclone="/synthetic/rclone",
            docker="/synthetic/docker",
            now=lambda: created_at,
        )
    assert not any("compose" in call for call, _ in fake.calls)


def test_hidden_identity_prompt_uses_owner_only_temporary_file_and_removes_it(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, _identity, compose, _remote, _created_at = _inputs(tmp_path)
    sentinel = "AGE-SECRET-KEY-SYNTHETIC-PRIVATE-VALUE"
    observed: dict[str, Any] = {}

    def fake_drill(**kwargs: Any) -> Mapping[str, Any]:
        identity = kwargs["identity"]
        assert isinstance(identity, Path)
        observed["path"] = identity
        observed["mode"] = identity.stat().st_mode & 0o777
        observed["value"] = identity.read_text(encoding="utf-8")
        return {"status": "verified"}

    def prompted(_prompt: str) -> str:
        return sentinel

    monkeypatch.setattr("scripts.run_restore_drill.getpass.getpass", prompted)
    monkeypatch.setattr("scripts.run_restore_drill.run_restore_drill", fake_drill)
    assert (
        main(
            [
                "--config",
                str(config),
                "--prompt-identity",
                "--compose-file",
                str(compose),
            ]
        )
        == 0
    )
    assert observed["mode"] == 0o600
    assert observed["value"] == sentinel + "\n"
    identity_path = observed["path"]
    assert isinstance(identity_path, Path)
    assert not identity_path.exists()
    output = capsys.readouterr()
    assert sentinel not in output.out
    assert sentinel not in output.err


def test_invalid_prompted_identity_is_redacted_and_never_runs(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    config, _identity, compose, _remote, _created_at = _inputs(tmp_path)
    sentinel = "not-an-age-key-private-value"

    def prompted(_prompt: str) -> str:
        return sentinel

    monkeypatch.setattr("scripts.run_restore_drill.getpass.getpass", prompted)

    def must_not_run(**_kwargs: Any) -> Mapping[str, Any]:
        raise AssertionError("drill must not run")

    monkeypatch.setattr("scripts.run_restore_drill.run_restore_drill", must_not_run)
    assert (
        main(
            [
                "--config",
                str(config),
                "--prompt-identity",
                "--compose-file",
                str(compose),
            ]
        )
        == 1
    )
    output = capsys.readouterr()
    assert "restore_identity_prompt_invalid" in output.err
    assert sentinel not in output.err
    assert sentinel not in output.out
