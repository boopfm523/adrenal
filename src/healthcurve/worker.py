"""Background worker entry point.

Runs the durable PostgreSQL job queue (ADR-0004) and, when configured, Telegram long
polling (ADR-0008). Both share one stop event so SIGTERM gives them a clean shutdown.

Like ``app.py`` and ``cli.py``, this sits outside the layered domain stack: an entry
point's job is to wire layers together.
"""

from __future__ import annotations

import signal
import sys
import threading
from types import FrameType

from healthcurve.config import Environment, Settings, TelegramMode, get_settings
from healthcurve.db import get_session_factory
from healthcurve.integrations.telegram import polling
from healthcurve.integrations.telegram.draft_jobs import (
    DRAFT_EXPIRY_TASK,
    make_draft_expiry_handler,
    schedule_draft_expiry,
)
from healthcurve.integrations.telegram.secrets import TelegramSecrets, load_telegram_secrets
from healthcurve.integrations.weather.jobs import WEATHER_ENRICHMENT_TASK, make_weather_handler
from healthcurve.logging import configure_logging, get_logger
from healthcurve.operations import worker as queue_worker

log = get_logger(__name__)

_stop = threading.Event()


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    log.info("worker stopping", reason_code=signal.Signals(signum).name)
    _stop.set()


def _run_telegram(settings: Settings, telegram_secrets: TelegramSecrets) -> None:
    try:
        polling.run(settings, telegram_secrets=telegram_secrets, stop_event=_stop)
    except Exception:
        log.error(
            "telegram poller crashed",
            integration="telegram",
            reason_code="telegram_poller_crashed",
            outcome="stopping",
        )
        _stop.set()


def main() -> int:
    settings = get_settings()
    configure_logging(json_output=settings.environment is not Environment.DEV)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    with get_session_factory()() as session:
        telegram_secrets = load_telegram_secrets(session, settings)
    polling_enabled = settings.telegram_mode is TelegramMode.POLLING and (
        telegram_secrets.configured_for(settings)
    )

    log.info(
        "worker started",
        outcome="running",
        integration="telegram" if polling_enabled else None,
        reason_code=None if polling_enabled else "telegram_not_configured",
        task="database_job_queue",
    )

    polling_thread: threading.Thread | None = None
    if polling_enabled:
        polling_thread = threading.Thread(
            target=_run_telegram,
            args=(settings, telegram_secrets),
            name="telegram-poller",
            daemon=True,
        )
        polling_thread.start()

    queue_worker.run_loop(
        get_session_factory(),
        {
            DRAFT_EXPIRY_TASK: make_draft_expiry_handler(),
            WEATHER_ENRICHMENT_TASK: make_weather_handler(),
        },
        stop_event=_stop,
        poll_interval_s=settings.job_poll_interval_s,
        schedulers=(schedule_draft_expiry,),
    )
    if polling_thread is not None:
        polling_thread.join(timeout=5)

    log.info("worker stopped", outcome="clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
