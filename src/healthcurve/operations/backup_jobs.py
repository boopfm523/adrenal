"""Nightly backup scheduling, execution, and privacy-safe health status."""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.operations.backup import (
    BackupConfig,
    BackupError,
    CommandRunner,
    Toolchain,
    create_backup,
)
from healthcurve.operations.jobs import Job, JobQueueError, JobStatus, enqueue
from healthcurve.operations.retention import (
    OffsiteSettings,
    OffsiteWriter,
    cleanup_local,
    discover_backup_sets,
    plan_retention,
    upload_backup_set,
)
from healthcurve.operations.worker import JobHandler

BACKUP_TASK = "backup.nightly"
BACKUP_WARNING_HOURS = 26.0


@dataclass(frozen=True)
class ScheduledBackupConfig:
    backup: BackupConfig
    offsite: OffsiteSettings
    retention_apply: bool = True

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> ScheduledBackupConfig:
        values = os.environ if env is None else env
        required = {
            "destination": values.get("HC_BACKUP_LOCAL_DIR", "").strip(),
            "recipient": values.get("HC_BACKUP_AGE_RECIPIENT", "").strip(),
            "uploads": values.get("HC_UPLOADS_DIR", "").strip(),
            "reports": values.get("HC_REPORT_ARTIFACTS_DIR", "").strip(),
        }
        if any(not value for value in required.values()):
            raise JobQueueError("backup_configuration_incomplete")
        project_dir = Path(values.get("HC_BACKUP_PROJECT_DIR", "/app"))
        work_root = Path(values.get("HC_BACKUP_WORK_ROOT", "/work"))
        path_values = (
            Path(required["destination"]),
            Path(required["uploads"]),
            Path(required["reports"]),
            project_dir,
            work_root,
        )
        if any(not path.is_absolute() for path in path_values):
            raise JobQueueError("backup_path_not_absolute")
        retention_value = values.get("HC_BACKUP_RETENTION_APPLY", "true").strip().lower()
        if retention_value not in {"true", "false"}:
            raise JobQueueError("backup_retention_setting_invalid")
        try:
            offsite = OffsiteSettings.from_env(values)
        except BackupError as exc:
            raise JobQueueError(exc.reason_code) from exc
        return cls(
            backup=BackupConfig(
                destination=Path(required["destination"]),
                work_root=work_root,
                recipient=required["recipient"],
                artifact_roots={
                    "uploads": Path(required["uploads"]),
                    "reports": Path(required["reports"]),
                },
                restore_files={
                    "docker-compose-yml": project_dir / "docker-compose.yml",
                    "alembic-ini": project_dir / "alembic.ini",
                    "caddyfile": project_dir / "deploy/Caddyfile",
                    "restore-canary-json": project_dir / "deploy/restore-canary.json",
                },
            ),
            offsite=offsite,
            retention_apply=retention_value == "true",
        )


@dataclass(frozen=True)
class BackupHealth:
    state: str
    age_hours: float | None
    last_success_at: datetime | None
    latest_job_status: JobStatus | None
    latest_job_error_code: str | None
    dead_letter: bool
    protected_set_count: int
    reason_codes: tuple[str, ...]


def schedule_nightly(session: Session, now: datetime, *, hour_utc: int = 2) -> Job:
    """Ensure exactly one scheduled backup exists for the current UTC day."""
    if now.tzinfo is None or now.utcoffset() is None or not 0 <= hour_utc <= 23:
        raise JobQueueError("backup_schedule_invalid")
    utc_now = now.astimezone(UTC)
    due = utc_now.replace(hour=hour_utc, minute=0, second=0, microsecond=0)
    day = due.date().isoformat()
    return enqueue(
        session,
        task=BACKUP_TASK,
        payload={"scheduled_day_utc": day},
        idempotency_key=f"nightly:{day}",
        run_at=due,
        priority=100,
        max_attempts=4,
    )


def make_backup_handler(
    config: ScheduledBackupConfig,
    *,
    writer: OffsiteWriter | None = None,
    tools: Toolchain | None = None,
    runner: CommandRunner | None = None,
) -> JobHandler:
    """Build the dedicated worker handler without exposing it to the general worker."""

    def handle(_session: Session, _payload: Mapping[str, object]) -> None:
        try:
            resolved_tools = tools or Toolchain.resolve()
            if runner is None:
                create_backup(config.backup, tools=resolved_tools)
            else:
                create_backup(config.backup, tools=resolved_tools, runner=runner)
            sets, protected = discover_backup_sets(config.backup.destination)
            if protected:
                raise BackupError("backup_integrity_anomaly")
            if config.offsite.enabled:
                if writer is None:
                    raise BackupError("offsite_writer_unavailable")
                for backup_set in sets:
                    upload_backup_set(
                        writer,
                        backup_set,
                        prefix=config.offsite.destination or "",
                    )
            cleanup_local(plan_retention(sets), apply=config.retention_apply)
        except BackupError as exc:
            raise JobQueueError(exc.reason_code) from exc

    return handle


def backup_health(
    session: Session,
    directory: Path,
    *,
    now: datetime,
    warning_hours: float = BACKUP_WARNING_HOURS,
) -> BackupHealth:
    """Return operational status only; never filenames, payloads, or health values."""
    if now.tzinfo is None or now.utcoffset() is None or warning_hours <= 0:
        raise JobQueueError("backup_health_input_invalid")
    measured_at = now.astimezone(UTC)
    sets, protected = discover_backup_sets(directory)
    newest = max(sets, key=lambda item: (item.created_at, item.set_id)) if sets else None
    age_hours = (
        max(0.0, (measured_at - newest.created_at.astimezone(UTC)).total_seconds() / 3600)
        if newest
        else None
    )
    latest_job = session.scalar(
        select(Job)
        .where(Job.task == BACKUP_TASK)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )

    reasons: list[str] = []
    if newest is None:
        reasons.append("backup_missing")
    elif age_hours is not None and age_hours >= warning_hours:
        reasons.append("backup_age_warning")
    if protected:
        reasons.append("backup_integrity_failed")
    if latest_job is not None and latest_job.status is JobStatus.DEAD_LETTER:
        reasons.append("backup_job_failed")

    state = "alert" if reasons else "healthy"
    return BackupHealth(
        state=state,
        age_hours=age_hours,
        last_success_at=newest.created_at if newest else None,
        latest_job_status=latest_job.status if latest_job else None,
        latest_job_error_code=latest_job.last_error_code if latest_job else None,
        dead_letter=latest_job is not None and latest_job.status is JobStatus.DEAD_LETTER,
        protected_set_count=len(protected),
        reason_codes=tuple(reasons),
    )
