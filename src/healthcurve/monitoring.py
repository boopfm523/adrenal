"""Privacy-safe operational snapshot and alert evaluation at the wiring layer."""

from __future__ import annotations

import shutil
from collections.abc import Callable
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Literal, Protocol

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from healthcurve.ai.ollama import OllamaClient
from healthcurve.config import Settings
from healthcurve.integrations.garmin.models import GarminImportBatch
from healthcurve.operations.backup_jobs import backup_health
from healthcurve.operations.jobs import queue_metrics
from healthcurve.operations.telemetry import (
    OperationalEvent,
    OperationalTelemetry,
    TelemetryUnavailable,
)

SignalState = Literal["healthy", "alert", "disabled"]


class DiskUsage(Protocol):
    @property
    def total(self) -> int: ...

    @property
    def used(self) -> int: ...

    @property
    def free(self) -> int: ...


@dataclass(frozen=True)
class OperationalSignal:
    name: str
    state: SignalState
    reason_code: str | None
    value: float | int | None
    unit: str | None


@dataclass(frozen=True)
class MonitoringSnapshot:
    state: Literal["healthy", "alert"]
    measured_at: datetime
    signals: tuple[OperationalSignal, ...]

    @property
    def reason_codes(self) -> tuple[str, ...]:
        return tuple(
            signal.reason_code
            for signal in self.signals
            if signal.state == "alert" and signal.reason_code is not None
        )

    def as_dict(self) -> dict[str, object]:
        return {
            "state": self.state,
            "measured_at": self.measured_at.isoformat(),
            "reason_codes": list(self.reason_codes),
            "signals": [asdict(signal) for signal in self.signals],
        }


def _threshold_signal(
    name: str,
    value: float | int,
    *,
    limit: float | int,
    reason_code: str,
    unit: str,
) -> OperationalSignal:
    alerted = value >= limit
    return OperationalSignal(
        name,
        "alert" if alerted else "healthy",
        reason_code if alerted else None,
        value,
        unit,
    )


def _telemetry_signals(
    telemetry: OperationalTelemetry, settings: Settings
) -> list[OperationalSignal]:
    policies = (
        (
            "request_errors_5m",
            OperationalEvent.REQUEST_ERROR,
            300,
            settings.monitor_request_error_limit,
            "request_errors_repeated",
        ),
        (
            "auth_failures_15m",
            OperationalEvent.AUTH_FAILURE,
            900,
            settings.monitor_auth_failure_limit,
            "auth_failures_repeated",
        ),
        (
            "model_failures_15m",
            OperationalEvent.MODEL_FAILURE,
            900,
            settings.monitor_model_failure_limit,
            "model_failures_repeated",
        ),
    )
    try:
        return [
            _threshold_signal(
                name,
                telemetry.count(event, window_seconds=window),
                limit=limit,
                reason_code=reason,
                unit="events",
            )
            for name, event, window, limit, reason in policies
        ]
    except TelemetryUnavailable:
        return [
            OperationalSignal(
                "operational_telemetry",
                "alert",
                "operational_telemetry_unavailable",
                None,
                None,
            )
        ]


def collect_snapshot(
    session: Session,
    settings: Settings,
    *,
    telemetry: OperationalTelemetry | None = None,
    now: datetime | None = None,
    model_health: Callable[[], bool] | None = None,
    disk_usage: Callable[[Path], DiskUsage] = shutil.disk_usage,
) -> MonitoringSnapshot:
    """Collect operational values only; no row payloads or owner data leave storage."""
    measured_at = (now or datetime.now(UTC)).astimezone(UTC)
    telemetry = telemetry or OperationalTelemetry(settings.redis_url)
    signals = _telemetry_signals(telemetry, settings)

    queue = queue_metrics(session, now=measured_at)
    queue_alert = queue.oldest_due_age_seconds >= settings.monitor_queue_age_limit_s
    dead_alert = queue.dead_letter_count > 0
    signals.extend(
        (
            OperationalSignal(
                "queue_oldest_due_age",
                "alert" if queue_alert else "healthy",
                "queue_stalled" if queue_alert else None,
                round(queue.oldest_due_age_seconds, 3),
                "seconds",
            ),
            OperationalSignal(
                "queue_dead_letters",
                "alert" if dead_alert else "healthy",
                "queue_dead_letter" if dead_alert else None,
                queue.dead_letter_count,
                "jobs",
            ),
        )
    )

    is_model_healthy = (model_health or OllamaClient(settings).health)()
    signals.append(
        OperationalSignal(
            "local_model",
            "healthy" if is_model_healthy else "alert",
            None if is_model_healthy else "model_unavailable",
            1 if is_model_healthy else 0,
            "boolean",
        )
    )

    if settings.monitor_garmin_age_limit_h is None:
        signals.append(OperationalSignal("garmin_import_age", "disabled", None, None, "hours"))
    else:
        latest = session.scalar(select(func.max(GarminImportBatch.confirmed_at)))
        age = (
            max(0.0, (measured_at - latest.astimezone(UTC)).total_seconds() / 3600)
            if latest is not None
            else None
        )
        stale = age is None or age >= settings.monitor_garmin_age_limit_h
        signals.append(
            OperationalSignal(
                "garmin_import_age",
                "alert" if stale else "healthy",
                "garmin_import_stopped" if stale else None,
                round(age, 3) if age is not None else None,
                "hours",
            )
        )

    if settings.backup_local_dir is None:
        signals.append(
            OperationalSignal(
                "backup_age", "alert", "backup_configuration_incomplete", None, "hours"
            )
        )
    else:
        backup = backup_health(
            session,
            settings.backup_local_dir,
            now=measured_at,
            warning_hours=settings.monitor_backup_age_limit_h,
        )
        signals.append(
            OperationalSignal(
                "backup_age",
                "alert" if backup.state == "alert" else "healthy",
                backup.reason_codes[0] if backup.reason_codes else None,
                round(backup.age_hours, 3) if backup.age_hours is not None else None,
                "hours",
            )
        )

    disk_path = settings.report_artifacts_dir
    while not disk_path.exists() and disk_path != disk_path.parent:
        disk_path = disk_path.parent
    usage = disk_usage(disk_path)
    free_percent = (usage.free / usage.total * 100) if usage.total else 0.0
    low_disk = free_percent <= settings.monitor_disk_free_percent
    signals.append(
        OperationalSignal(
            "disk_free",
            "alert" if low_disk else "healthy",
            "disk_space_low" if low_disk else None,
            round(free_percent, 3),
            "percent",
        )
    )

    state: Literal["healthy", "alert"] = (
        "alert" if any(signal.state == "alert" for signal in signals) else "healthy"
    )
    return MonitoringSnapshot(state, measured_at, tuple(signals))
