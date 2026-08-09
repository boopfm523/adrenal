"""Dedicated nightly-backup queue worker.

This process alone receives the backup database credential, age recipient, artifact
mounts, and backup destination. The API and general worker never receive them.
"""

from __future__ import annotations

import signal
import sys
import threading
from types import FrameType

from healthcurve.config import Environment, get_settings
from healthcurve.db import get_session_factory
from healthcurve.logging import configure_logging, get_logger
from healthcurve.operations import worker
from healthcurve.operations.backup_jobs import (
    BACKUP_TASK,
    ScheduledBackupConfig,
    make_backup_handler,
    schedule_nightly,
)
from healthcurve.operations.jobs import JobQueueError

log = get_logger(__name__)
_stop = threading.Event()


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    log.info("backup worker stopping", reason_code=signal.Signals(signum).name)
    _stop.set()


def main() -> int:
    settings = get_settings()
    configure_logging(json_output=settings.environment is not Environment.DEV)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    try:
        config = ScheduledBackupConfig.from_env()
    except JobQueueError as exc:
        log.error(
            "backup worker configuration rejected",
            task=BACKUP_TASK,
            reason_code=exc.reason_code,
            outcome="stopped",
        )
        return 1

    log.info("backup worker started", task=BACKUP_TASK, outcome="running")
    worker.run_loop(
        get_session_factory(),
        {BACKUP_TASK: make_backup_handler(config)},
        stop_event=_stop,
        poll_interval_s=settings.job_poll_interval_s,
        worker_id="dedicated-backup-worker",
        schedulers=(schedule_nightly,),
    )
    log.info("backup worker stopped", task=BACKUP_TASK, outcome="clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
