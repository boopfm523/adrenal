"""Scheduled expiry for unconfirmed AI extraction drafts.

The job payload is deliberately empty of owner or health data. The durable
``ops.job`` row provides enough operational evidence to see whether expiry ran,
while the handler removes class-C9 raw text in the same transaction that marks the
job complete.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.integrations.telegram.handlers import expire_stale_drafts
from healthcurve.operations.jobs import Job, JobQueueError, JobStatus, enqueue
from healthcurve.operations.worker import JobHandler

DRAFT_EXPIRY_TASK = "ai.drafts.expire"
DRAFT_EXPIRY_INTERVAL_MINUTES = 15


@dataclass(frozen=True)
class DraftExpiryHealth:
    """Privacy-safe status for the most recently scheduled expiry job."""

    latest_job_status: JobStatus | None
    scheduled_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    latest_job_error_code: str | None


def schedule_draft_expiry(session: Session, now: datetime) -> Job:
    """Ensure one expiry job exists for the current fifteen-minute UTC bucket."""
    if now.tzinfo is None or now.utcoffset() is None:
        raise JobQueueError("draft_expiry_schedule_invalid")
    utc_now = now.astimezone(UTC)
    bucket_minute = (
        utc_now.minute // DRAFT_EXPIRY_INTERVAL_MINUTES
    ) * DRAFT_EXPIRY_INTERVAL_MINUTES
    bucket = utc_now.replace(minute=bucket_minute, second=0, microsecond=0)
    bucket_key = bucket.isoformat().replace("+00:00", "Z")
    return enqueue(
        session,
        task=DRAFT_EXPIRY_TASK,
        payload={"scheduled_at_utc": bucket_key},
        idempotency_key=f"quarter-hour:{bucket_key}",
        run_at=bucket,
        priority=50,
        max_attempts=4,
    )


def make_draft_expiry_handler(
    *, clock: Callable[[], datetime] = lambda: datetime.now(UTC)
) -> JobHandler:
    """Build the handler with an injectable clock for deterministic verification."""

    def handle(session: Session, _payload: Mapping[str, object]) -> None:
        expire_stale_drafts(session, now=clock())

    return handle


def draft_expiry_health(session: Session) -> DraftExpiryHealth:
    """Return the latest job state without exposing payloads or health content."""
    latest = session.scalar(
        select(Job)
        .where(Job.task == DRAFT_EXPIRY_TASK)
        .order_by(Job.created_at.desc(), Job.id.desc())
        .limit(1)
    )
    if latest is None:
        return DraftExpiryHealth(None, None, None, None, None)
    return DraftExpiryHealth(
        latest_job_status=latest.status,
        scheduled_at=latest.run_at,
        started_at=latest.started_at,
        finished_at=latest.finished_at,
        latest_job_error_code=latest.last_error_code,
    )
