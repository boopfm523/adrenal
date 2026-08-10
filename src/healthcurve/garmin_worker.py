"""Dedicated least-privilege process for Garmin-only outbound synchronization."""

from __future__ import annotations

import signal
import sys
import threading
from types import FrameType

from healthcurve.config import Environment, get_settings
from healthcurve.db import get_session_factory
from healthcurve.integrations.garmin.connect_jobs import (
    GARMIN_DISCONNECT_TASK,
    GARMIN_SYNC_TASK,
    make_disconnect_handler,
    make_garmin_handler,
    schedule_garmin_sync,
)
from healthcurve.logging import configure_logging, get_logger
from healthcurve.operations import worker as queue_worker

log = get_logger(__name__)
_stop = threading.Event()


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    log.info("garmin worker stopping", reason_code=signal.Signals(signum).name)
    _stop.set()


def main() -> int:
    settings = get_settings()
    configure_logging(json_output=settings.environment is not Environment.DEV)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    if not settings.garmin_enabled:
        log.info("garmin worker disabled", reason_code="garmin_not_enabled", outcome="clean")
        return 0
    if settings.garmin_token_store is None:
        log.error(
            "garmin worker configuration invalid",
            reason_code="garmin_token_store_not_configured",
            outcome="failed",
        )
        return 1
    queue_worker.run_loop(
        get_session_factory(),
        {
            GARMIN_SYNC_TASK: make_garmin_handler(settings),
            GARMIN_DISCONNECT_TASK: make_disconnect_handler(settings),
        },
        stop_event=_stop,
        poll_interval_s=settings.job_poll_interval_s,
        worker_id="garmin-worker",
        schedulers=(lambda session, now: schedule_garmin_sync(session, now, settings=settings),),
    )
    log.info("garmin worker stopped", outcome="clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
