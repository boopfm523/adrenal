"""Least-privilege worker for private storage deletion jobs.

This process can reach PostgreSQL on an internal-only Docker network and can write the
uploads/report volumes. It has no Telegram, Redis, Ollama, provider, or internet path.
"""

from __future__ import annotations

import signal
import sys
import threading
from pathlib import Path
from types import FrameType

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.orm import sessionmaker

from healthcurve.config import Environment
from healthcurve.db import build_engine
from healthcurve.labs.cleanup_jobs import (
    LAB_DOCUMENT_CLEANUP_TASK,
    make_document_cleanup_handler,
)
from healthcurve.labs.documents import DocumentLayout
from healthcurve.logging import configure_logging, get_logger
from healthcurve.operations import worker as queue_worker
from healthcurve.reports.cleanup_jobs import (
    REPORT_ARTIFACT_CLEANUP_TASK,
    make_snapshot_artifact_cleanup_handler,
)

log = get_logger(__name__)
_stop = threading.Event()


class CleanupSettings(BaseSettings):
    """Minimal configuration for a worker that must not inherit app credentials."""

    model_config = SettingsConfigDict(
        env_prefix="HC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEV
    database_url: str = "postgresql+psycopg://healthcurve@localhost:5432/healthcurve"
    job_poll_interval_s: float = Field(default=2.0, gt=0, le=60)
    uploads_dir: Path = Path("var/uploads")
    report_artifacts_dir: Path = Path("var/reports")


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    log.info("cleanup worker stopping", reason_code=signal.Signals(signum).name)
    _stop.set()


def main() -> int:
    settings = CleanupSettings()
    session_factory = sessionmaker(build_engine(settings.database_url), expire_on_commit=False)
    configure_logging(json_output=settings.environment is not Environment.DEV)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    log.info("cleanup worker started", outcome="running", task="private_storage_cleanup")
    queue_worker.run_loop(
        session_factory,
        {
            LAB_DOCUMENT_CLEANUP_TASK: make_document_cleanup_handler(
                DocumentLayout(settings.uploads_dir)
            ),
            REPORT_ARTIFACT_CLEANUP_TASK: make_snapshot_artifact_cleanup_handler(
                settings.report_artifacts_dir
            ),
        },
        stop_event=_stop,
        poll_interval_s=settings.job_poll_interval_s,
    )
    log.info("cleanup worker stopped", outcome="clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
