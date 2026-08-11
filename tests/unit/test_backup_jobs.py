from __future__ import annotations

import subprocess
from collections.abc import Mapping, Sequence
from pathlib import Path
from unittest.mock import Mock

import pytest
from sqlalchemy.orm import Session

from healthcurve.operations.backup import BackupConfig, Toolchain
from healthcurve.operations.backup_jobs import ScheduledBackupConfig, make_backup_handler
from healthcurve.operations.jobs import JobQueueError
from healthcurve.operations.retention import OffsiteSettings

TOOLS = Toolchain("fake-pg-dump", "fake-pg-restore", "fake-age")


class SyntheticCommands:
    def __init__(self, *, fail_dump: bool = False) -> None:
        self.fail_dump = fail_dump

    def __call__(
        self, args: Sequence[str], _env: Mapping[str, str]
    ) -> subprocess.CompletedProcess[str]:
        if args[0] == "fake-pg-dump":
            if self.fail_dump:
                return subprocess.CompletedProcess(args, 1, "", "private database URL")
            output = Path(
                next(value.split("=", 1)[1] for value in args if value.startswith("--file="))
            )
            output.write_bytes(b"synthetic dump")
            return subprocess.CompletedProcess(args, 0, "", "")
        if args[0] == "fake-pg-restore":
            inventory = "\n".join(
                f"1; 2615 1 SCHEMA - {schema} healthcurve"
                for schema in ("identity", "fact", "plan", "ai", "ops")
            )
            return subprocess.CompletedProcess(args, 0, inventory, "")
        if args[0] == "fake-age":
            output = Path(args[args.index("--output") + 1])
            output.write_bytes(b"synthetic encrypted archive")
            return subprocess.CompletedProcess(args, 0, "", "")
        raise AssertionError(f"unexpected command {args[0]}")


def _config(tmp_path: Path, *, offsite: bool = False) -> ScheduledBackupConfig:
    destination = tmp_path / "backups"
    work = tmp_path / "work"
    uploads = tmp_path / "uploads"
    reports = tmp_path / "reports"
    project = tmp_path / "project"
    uploads.mkdir()
    reports.mkdir()
    (project / "deploy").mkdir(parents=True)
    (project / "docker-compose.yml").write_text("services: {}", encoding="utf-8")
    (project / "alembic.ini").write_text("[alembic]", encoding="utf-8")
    (project / "deploy/Caddyfile").write_text("synthetic", encoding="utf-8")
    return ScheduledBackupConfig(
        backup=BackupConfig(
            destination=destination,
            work_root=work,
            recipient="age1syntheticpublicrecipient",
            artifact_roots={"uploads": uploads, "reports": reports},
            restore_files={
                "docker-compose-yml": project / "docker-compose.yml",
                "alembic-ini": project / "alembic.ini",
                "caddyfile": project / "deploy/Caddyfile",
            },
        ),
        offsite=OffsiteSettings(
            enabled=offsite,
            provider="synthetic" if offsite else None,
            destination="synthetic-prefix" if offsite else None,
        ),
    )


def test_scheduled_handler_creates_and_retains_verified_local_set(tmp_path: Path) -> None:
    config = _config(tmp_path)
    handler = make_backup_handler(config, tools=TOOLS, runner=SyntheticCommands())
    handler(Mock(spec=Session), {"scheduled_day_utc": "2026-08-09"})
    assert len(list(config.backup.destination.glob("*.tar.age"))) == 1
    assert len(list(config.backup.destination.glob("*.json"))) == 1
    assert list(config.backup.work_root.iterdir()) == []


def test_scheduled_handler_redacts_tool_failure(tmp_path: Path) -> None:
    handler = make_backup_handler(
        _config(tmp_path), tools=TOOLS, runner=SyntheticCommands(fail_dump=True)
    )
    with pytest.raises(JobQueueError, match=r"^database_dump_failed$") as error:
        handler(Mock(spec=Session), {})
    assert "private" not in str(error.value)


def test_enabled_offsite_fails_closed_without_writer(tmp_path: Path) -> None:
    handler = make_backup_handler(
        _config(tmp_path, offsite=True), tools=TOOLS, runner=SyntheticCommands()
    )
    with pytest.raises(JobQueueError, match=r"^offsite_writer_unavailable$"):
        handler(Mock(spec=Session), {})


def test_scheduled_config_requires_all_local_inputs() -> None:
    with pytest.raises(JobQueueError, match=r"^backup_configuration_incomplete$"):
        ScheduledBackupConfig.from_env({})


def test_scheduled_config_rejects_invalid_retention_switch(tmp_path: Path) -> None:
    values = {
        "HC_BACKUP_LOCAL_DIR": str(tmp_path / "backups"),
        "HC_BACKUP_AGE_RECIPIENT": "age1synthetic",
        "HC_UPLOADS_DIR": str(tmp_path / "uploads"),
        "HC_REPORT_ARTIFACTS_DIR": str(tmp_path / "reports"),
        "HC_BACKUP_RETENTION_APPLY": "maybe",
    }
    with pytest.raises(JobQueueError, match=r"^backup_retention_setting_invalid$"):
        ScheduledBackupConfig.from_env(values)


def test_scheduled_config_includes_non_health_restore_canary(tmp_path: Path) -> None:
    project = tmp_path / "project"
    values = {
        "HC_BACKUP_LOCAL_DIR": str(tmp_path / "backups"),
        "HC_BACKUP_AGE_RECIPIENT": "age1synthetic",
        "HC_UPLOADS_DIR": str(tmp_path / "uploads"),
        "HC_REPORT_ARTIFACTS_DIR": str(tmp_path / "reports"),
        "HC_BACKUP_PROJECT_DIR": str(project),
    }
    config = ScheduledBackupConfig.from_env(values)
    assert config.backup.restore_files["restore-canary-json"] == (
        project / "deploy/restore-canary.json"
    )


def test_scheduled_config_rejects_relative_storage_paths(tmp_path: Path) -> None:
    values = {
        "HC_BACKUP_LOCAL_DIR": "relative/backups",
        "HC_BACKUP_AGE_RECIPIENT": "age1synthetic",
        "HC_UPLOADS_DIR": str(tmp_path / "uploads"),
        "HC_REPORT_ARTIFACTS_DIR": str(tmp_path / "reports"),
    }
    with pytest.raises(JobQueueError, match=r"^backup_path_not_absolute$"):
        ScheduledBackupConfig.from_env(values)


def test_scheduled_config_redacts_offsite_configuration_failure(tmp_path: Path) -> None:
    values = {
        "HC_BACKUP_LOCAL_DIR": str(tmp_path / "backups"),
        "HC_BACKUP_AGE_RECIPIENT": "age1synthetic",
        "HC_UPLOADS_DIR": str(tmp_path / "uploads"),
        "HC_REPORT_ARTIFACTS_DIR": str(tmp_path / "reports"),
        "HC_BACKUP_OFFSITE_ENABLED": "true",
    }
    with pytest.raises(JobQueueError, match=r"^offsite_configuration_incomplete$"):
        ScheduledBackupConfig.from_env(values)
