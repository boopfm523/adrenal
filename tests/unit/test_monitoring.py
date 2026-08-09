from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import NamedTuple, cast
from unittest import mock

import pytest
import yaml
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session

from healthcurve import monitor as monitor_runner
from healthcurve import monitoring
from healthcurve.app import create_app
from healthcurve.config import Settings
from healthcurve.operations.backup_jobs import BackupHealth
from healthcurve.operations.jobs import QueueMetrics
from healthcurve.operations.telemetry import OperationalEvent, OperationalTelemetry

NOW = datetime(2026, 8, 9, 20, 0, tzinfo=UTC)


class Disk(NamedTuple):
    total: int
    used: int
    free: int


def _settings(tmp_path: Path, **overrides: object) -> Settings:
    base: dict[str, object] = {
        "_env_file": None,
        "ollama_base_url": "http://ollama:11434",
        "backup_local_dir": tmp_path / "backups",
        "report_artifacts_dir": tmp_path,
        "monitor_garmin_age_limit_h": 36,
    }
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


def _backup(state: str = "healthy") -> BackupHealth:
    return BackupHealth(
        state=state,
        age_hours=2 if state == "healthy" else 30,
        last_success_at=NOW - timedelta(hours=2),
        latest_job_status=None,
        latest_job_error_code=None,
        dead_letter=False,
        protected_set_count=0,
        reason_codes=() if state == "healthy" else ("backup_age_warning",),
    )


def test_healthy_snapshot_contains_only_operational_signals(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry = mock.MagicMock(spec=OperationalTelemetry)
    telemetry.count.side_effect = [0, 0, 0]
    monkeypatch.setattr(
        monitoring, "queue_metrics", mock.MagicMock(return_value=QueueMetrics(0, 0, 0, 0))
    )
    monkeypatch.setattr(monitoring, "backup_health", mock.MagicMock(return_value=_backup()))
    session = mock.MagicMock(spec=Session)
    session.scalar.return_value = NOW - timedelta(hours=1)

    snapshot = monitoring.collect_snapshot(
        cast(Session, session),
        _settings(tmp_path),
        telemetry=telemetry,
        now=NOW,
        model_health=lambda: True,
        disk_usage=lambda _path: Disk(100, 20, 80),
    )

    assert snapshot.state == "healthy"
    assert snapshot.reason_codes == ()
    payload = snapshot.as_dict()
    rendered = str(payload).lower()
    assert "owner@" not in rendered
    assert "prompt" not in rendered
    assert "symptom" not in rendered


def test_controlled_failures_trigger_every_required_alert(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    telemetry = mock.MagicMock(spec=OperationalTelemetry)
    telemetry.count.side_effect = [5, 5, 3]
    monkeypatch.setattr(
        monitoring, "queue_metrics", mock.MagicMock(return_value=QueueMetrics(2, 0, 1, 301))
    )
    monkeypatch.setattr(monitoring, "backup_health", mock.MagicMock(return_value=_backup("alert")))
    session = mock.MagicMock(spec=Session)
    session.scalar.return_value = NOW - timedelta(hours=37)

    snapshot = monitoring.collect_snapshot(
        cast(Session, session),
        _settings(tmp_path),
        telemetry=telemetry,
        now=NOW,
        model_health=lambda: False,
        disk_usage=lambda _path: Disk(100, 95, 5),
    )

    assert snapshot.state == "alert"
    assert set(snapshot.reason_codes) == {
        "request_errors_repeated",
        "auth_failures_repeated",
        "model_failures_repeated",
        "queue_stalled",
        "queue_dead_letter",
        "model_unavailable",
        "garmin_import_stopped",
        "backup_age_warning",
        "disk_space_low",
    }


def test_telemetry_records_only_allowlisted_event_and_server_time() -> None:
    client = mock.MagicMock()
    client.time.return_value = (1000, 0)
    client.zcount.return_value = 4
    with mock.patch("healthcurve.operations.telemetry.Redis.from_url", return_value=client):
        telemetry = OperationalTelemetry("redis://redis:6379/0")

    telemetry.record(OperationalEvent.AUTH_FAILURE)
    assert telemetry.count(OperationalEvent.AUTH_FAILURE, window_seconds=900) == 4
    calls = " ".join(str(call) for call in client.method_calls)
    assert "hc:telemetry:auth_failure" in calls
    assert "email" not in calls
    assert "owner" not in calls


def test_monitor_compose_is_private_and_hardened() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "deploy/monitoring.compose.yml").read_text())
    service = compose["services"]["monitor"]

    assert "ports" not in service
    assert service["read_only"] is True
    assert service["cap_drop"] == ["ALL"]
    assert service["security_opt"] == ["no-new-privileges:true"]
    assert any(volume.endswith(":ro") for volume in service["volumes"])


def test_server_errors_increment_privacy_safe_telemetry() -> None:
    app = create_app(
        Settings(_env_file=None, ollama_base_url="http://ollama:11434")  # type: ignore[call-arg]
    )
    telemetry = mock.MagicMock(spec=OperationalTelemetry)
    app.state.telemetry = telemetry

    @app.get("/synthetic-server-error")
    def synthetic_server_error() -> JSONResponse:
        return JSONResponse({"detail": "synthetic"}, status_code=500)

    response = TestClient(app).get("/synthetic-server-error")

    assert response.status_code == 500
    telemetry.record.assert_called_once_with(OperationalEvent.REQUEST_ERROR)


def test_alert_snapshot_is_delivered_off_host_via_telegram(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    snapshot = monitoring.MonitoringSnapshot(
        "alert",
        NOW,
        (monitoring.OperationalSignal("disk_free", "alert", "disk_space_low", 4.2, "percent"),),
    )
    telegram = mock.MagicMock()
    telegram.send_message.return_value = True
    monkeypatch.setattr(
        monitor_runner, "_load_alert_delivery", mock.MagicMock(return_value=(telegram, 123))
    )
    monkeypatch.setattr(monitor_runner, "_snapshot", mock.MagicMock(return_value=snapshot))

    result = monitor_runner.run(_settings(tmp_path), once=True)

    assert result == 2
    message = telegram.send_message.call_args.args[1]
    assert "disk_space_low" in message
    assert "4.2" not in message
    assert "owner" not in message.lower()
