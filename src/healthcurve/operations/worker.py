"""Small durable worker loop for the ADR-0004 queue."""

from __future__ import annotations

import socket
import threading
from collections.abc import Callable, Mapping, Sequence
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session, sessionmaker

from healthcurve.logging import get_logger
from healthcurve.operations.jobs import (
    ClaimedJob,
    JobQueueError,
    claim,
    complete,
    fail,
)

log = get_logger(__name__)

type JobHandler = Callable[[Session, Mapping[str, Any]], None]
type JobScheduler = Callable[[Session, datetime], object]


def _failure_reason(exc: Exception) -> str:
    if isinstance(exc, JobQueueError):
        return exc.reason_code
    return "handler_failed"


def run_once(
    factory: sessionmaker[Session],
    handlers: Mapping[str, JobHandler],
    *,
    worker_id: str,
    lease_duration: timedelta = timedelta(minutes=5),
) -> ClaimedJob | None:
    """Claim and execute at most one job; domain writes and completion are atomic."""
    with factory() as session, session.begin():
        claimed = claim(
            session,
            worker_id=worker_id,
            lease_duration=lease_duration,
            tasks=handlers.keys(),
        )
    if claimed is None:
        return None

    try:
        handler = handlers.get(claimed.task)
        if handler is None:
            raise JobQueueError("handler_not_registered")
        with factory() as session, session.begin():
            handler(session, claimed.payload)
            complete(session, claimed)
    except Exception as exc:
        reason_code = _failure_reason(exc)
        try:
            with factory() as session, session.begin():
                status = fail(session, claimed, reason_code=reason_code)
        except JobQueueError as lease_error:
            log.warning(
                "job failure could not be recorded",
                job_id=str(claimed.id),
                task=claimed.task,
                reason_code=lease_error.reason_code,
                outcome="lease_lost",
            )
        else:
            log.warning(
                "job attempt failed",
                job_id=str(claimed.id),
                task=claimed.task,
                attempt=claimed.attempt,
                max_attempts=claimed.max_attempts,
                reason_code=reason_code,
                outcome=status.value,
            )
    else:
        log.info(
            "job completed",
            job_id=str(claimed.id),
            task=claimed.task,
            attempt=claimed.attempt,
            max_attempts=claimed.max_attempts,
            outcome="completed",
        )
    return claimed


def run_loop(
    factory: sessionmaker[Session],
    handlers: Mapping[str, JobHandler],
    *,
    stop_event: threading.Event,
    poll_interval_s: float,
    worker_id: str | None = None,
    schedulers: Sequence[JobScheduler] = (),
    lease_duration: timedelta = timedelta(minutes=5),
) -> None:
    """Poll until stopped, containing transient database failures without busy-looping."""
    identifier = worker_id or f"{socket.gethostname()}-queue"
    while not stop_event.is_set():
        try:
            if schedulers:
                with factory() as session, session.begin():
                    scheduled_at = datetime.now(UTC)
                    for scheduler in schedulers:
                        scheduler(session, scheduled_at)
            claimed = run_once(
                factory,
                handlers,
                worker_id=identifier,
                lease_duration=lease_duration,
            )
        except Exception:
            # Database/driver text can contain URLs. The allow-listed code is enough
            # for an alert; details remain in local database/server diagnostics.
            log.error(
                "job queue poll failed",
                task="database_job_queue",
                reason_code="job_queue_poll_failed",
                outcome="retrying",
            )
            stop_event.wait(poll_interval_s)
            continue

        if claimed is None:
            stop_event.wait(poll_interval_s)
