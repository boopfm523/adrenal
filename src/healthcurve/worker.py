"""Background worker entry point.

Runs the Telegram long poller (ADR-0008) when Telegram is configured in polling mode.
Nothing else runs here yet -- the job queue described in ADR-0004 is still a tracked
issue, and this process deliberately does not pretend otherwise.

Like ``app.py`` and ``cli.py``, this sits outside the layered domain stack: an entry
point's job is to wire layers together.
"""

from __future__ import annotations

import signal
import sys
import threading
from types import FrameType

from healthcurve.config import Environment, TelegramMode, get_settings
from healthcurve.integrations.telegram import polling
from healthcurve.logging import configure_logging, get_logger

log = get_logger(__name__)

_stop = threading.Event()


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    log.info("worker stopping", reason_code=signal.Signals(signum).name)
    _stop.set()


def main() -> int:
    settings = get_settings()
    configure_logging(json_output=settings.environment is not Environment.DEV)

    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)

    polling_enabled = (
        settings.telegram_mode is TelegramMode.POLLING and settings.telegram_configured
    )

    log.info(
        "worker started",
        outcome="polling" if polling_enabled else "idle",
        integration="telegram" if polling_enabled else None,
        reason_code=None if polling_enabled else "telegram_not_configured",
        task="job queue pending -- see ADR-0004",
    )

    if polling_enabled:
        # Blocks until stopped. Its own failures are handled internally; anything that
        # escapes is a real bug and should surface rather than be swallowed here.
        polling.run(settings, stop_event=_stop)
    else:
        # Idle rather than exit: exiting would restart-loop under compose, and a loop
        # would hide a real crash behind a wall of restarts.
        _stop.wait()

    log.info("worker stopped", outcome="clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
