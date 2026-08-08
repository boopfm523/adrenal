"""Background worker entrypoint.

The real claim loop -- ``SELECT ... FOR UPDATE SKIP LOCKED`` over ``ops.job``, with
bounded retries and a dead-letter path -- is specified in ADR-0004 and not yet built.

This placeholder exists so the local stack comes up cleanly instead of crash-looping,
which would make a genuine worker failure indistinguishable from an unbuilt one. It
does no work and claims no jobs; it logs once, then idles until told to stop.
"""

from __future__ import annotations

import signal
import sys
import threading
from types import FrameType

from healthcurve.config import Environment, get_settings
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

    log.info(
        "worker started",
        outcome="idle",
        reason_code="job_queue_not_implemented",
        task="see ADR-0004",
    )

    # Idle rather than exit: exiting would restart-loop under compose, and looping
    # would hide a real crash behind a wall of restarts.
    _stop.wait()
    log.info("worker stopped", outcome="clean")
    return 0


if __name__ == "__main__":
    sys.exit(main())
