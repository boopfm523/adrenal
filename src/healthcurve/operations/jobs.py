"""PostgreSQL-backed durable job queue (ADR-0004).

Callers pass their existing :class:`~sqlalchemy.orm.Session` to :func:`enqueue`, so
the job and the row that justifies it commit or roll back together. Workers claim with
``FOR UPDATE SKIP LOCKED`` and carry an opaque lease token through completion/failure.
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Collection, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Final

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Index,
    Integer,
    SmallInteger,
    String,
    UniqueConstraint,
    func,
    or_,
    select,
)
from sqlalchemy.dialects.postgresql import JSONB, insert
from sqlalchemy.orm import Mapped, Session, mapped_column

from healthcurve.db import OPS_SCHEMA, OpsBase, StrEnumType

SAFE_REASON: Final = re.compile(r"^[a-z][a-z0-9_]{0,63}$")


class JobStatus(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    COMPLETED = "completed"
    DEAD_LETTER = "dead_letter"


class JobQueueError(RuntimeError):
    """Privacy-safe queue failure carrying only an operational reason code."""

    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


class Job(OpsBase):
    __tablename__ = "job"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    task: Mapped[str] = mapped_column(String(120), nullable=False)
    payload: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False, default=dict)
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[JobStatus] = mapped_column(
        StrEnumType(JobStatus, 16), nullable=False, default=JobStatus.QUEUED
    )
    priority: Mapped[int] = mapped_column(SmallInteger, nullable=False, default=0)
    attempt_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=5)

    run_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(UTC)
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    lease_token: Mapped[uuid.UUID | None] = mapped_column()
    worker_id: Mapped[str | None] = mapped_column(String(120))

    #: A stable operational code only, never exception text or health-bearing payload.
    last_error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (
        UniqueConstraint("task", "idempotency_key", name="uq_job_task_idempotency_key"),
        CheckConstraint("attempt_count >= 0", name="attempt_count_nonnegative"),
        CheckConstraint("max_attempts BETWEEN 1 AND 100", name="max_attempts_bounded"),
        Index("ix_job_claim", "status", "run_at", "priority", "created_at"),
        OPS_SCHEMA,
    )


@dataclass(frozen=True)
class ClaimedJob:
    id: uuid.UUID
    task: str
    payload: Mapping[str, Any]
    attempt: int
    max_attempts: int
    lease_token: uuid.UUID


@dataclass(frozen=True)
class QueueMetrics:
    queued_count: int
    running_count: int
    dead_letter_count: int
    oldest_due_age_seconds: float


@dataclass(frozen=True)
class DeadLetter:
    id: uuid.UUID
    task: str
    attempt_count: int
    max_attempts: int
    last_error_code: str | None
    finished_at: datetime | None


@dataclass(frozen=True)
class RetryPolicy:
    base_delay: timedelta = timedelta(seconds=30)
    max_delay: timedelta = timedelta(hours=1)

    def delay_for(self, attempt: int) -> timedelta:
        if attempt < 1:
            raise JobQueueError("retry_attempt_invalid")
        seconds = min(
            self.base_delay.total_seconds() * (2 ** (attempt - 1)),
            self.max_delay.total_seconds(),
        )
        return timedelta(seconds=seconds)


def _utc_now(value: datetime | None = None) -> datetime:
    now = value or datetime.now(UTC)
    if now.tzinfo is None or now.utcoffset() is None:
        raise JobQueueError("job_time_naive")
    return now.astimezone(UTC)


def enqueue(
    session: Session,
    *,
    task: str,
    payload: Mapping[str, Any],
    idempotency_key: str,
    run_at: datetime | None = None,
    priority: int = 0,
    max_attempts: int = 5,
) -> Job:
    """Enqueue in the caller's transaction; an existing key is a true no-op."""
    if not task or len(task) > 120 or not idempotency_key or len(idempotency_key) > 255:
        raise JobQueueError("job_identity_invalid")
    if not -32768 <= priority <= 32767 or not 1 <= max_attempts <= 100:
        raise JobQueueError("job_policy_invalid")
    due = _utc_now(run_at)
    identifier = uuid.uuid4()
    statement = (
        insert(Job)
        .values(
            id=identifier,
            task=task,
            payload=dict(payload),
            idempotency_key=idempotency_key,
            status=JobStatus.QUEUED,
            priority=priority,
            attempt_count=0,
            max_attempts=max_attempts,
            run_at=due,
        )
        .on_conflict_do_nothing(index_elements=[Job.task, Job.idempotency_key])
        .returning(Job.id)
    )
    created_id = session.scalar(statement)
    if created_id is not None:
        job = session.get(Job, created_id)
    else:
        job = session.scalar(
            select(Job).where(Job.task == task, Job.idempotency_key == idempotency_key)
        )
    if job is None:
        raise JobQueueError("job_enqueue_conflict")
    return job


def claim(
    session: Session,
    *,
    worker_id: str,
    now: datetime | None = None,
    lease_duration: timedelta = timedelta(minutes=5),
    tasks: Collection[str] | None = None,
) -> ClaimedJob | None:
    """Claim one due job without waiting on another worker's locked candidate."""
    if not worker_id or len(worker_id) > 120 or lease_duration <= timedelta(0):
        raise JobQueueError("job_worker_invalid")
    if tasks is not None and not tasks:
        return None
    claimed_at = _utc_now(now)

    available = or_(
        (Job.status == JobStatus.QUEUED) & (Job.run_at <= claimed_at),
        (Job.status == JobStatus.RUNNING) & (Job.lease_expires_at <= claimed_at),
    )
    if tasks is not None:
        available &= Job.task.in_(tuple(tasks))

    while True:
        job = session.scalar(
            select(Job)
            .where(available)
            .order_by(Job.priority.desc(), Job.run_at, Job.created_at, Job.id)
            .with_for_update(skip_locked=True)
            .limit(1)
        )
        if job is None:
            return None
        if job.status is JobStatus.RUNNING and job.attempt_count >= job.max_attempts:
            # A worker vanished on its final attempt. Mark only the row obtained via
            # SKIP LOCKED, then continue to another due job without blocking.
            job.status = JobStatus.DEAD_LETTER
            job.finished_at = claimed_at
            job.lease_expires_at = None
            job.lease_token = None
            job.worker_id = None
            job.last_error_code = "lease_expired"
            session.flush()
            continue
        break

    token = uuid.uuid4()
    job.status = JobStatus.RUNNING
    job.attempt_count += 1
    job.started_at = job.started_at or claimed_at
    job.finished_at = None
    job.lease_expires_at = claimed_at + lease_duration
    job.lease_token = token
    job.worker_id = worker_id
    job.last_error_code = None
    session.flush()
    return ClaimedJob(
        id=job.id,
        task=job.task,
        payload=dict(job.payload),
        attempt=job.attempt_count,
        max_attempts=job.max_attempts,
        lease_token=token,
    )


def _locked_job(session: Session, claimed: ClaimedJob) -> Job:
    job = session.scalar(select(Job).where(Job.id == claimed.id).with_for_update())
    if job is None or job.status is not JobStatus.RUNNING or job.lease_token != claimed.lease_token:
        raise JobQueueError("job_lease_lost")
    return job


def complete(session: Session, claimed: ClaimedJob, *, now: datetime | None = None) -> None:
    job = _locked_job(session, claimed)
    finished = _utc_now(now)
    job.status = JobStatus.COMPLETED
    job.finished_at = finished
    job.lease_expires_at = None
    job.lease_token = None
    job.worker_id = None
    job.last_error_code = None


def fail(
    session: Session,
    claimed: ClaimedJob,
    *,
    reason_code: str,
    now: datetime | None = None,
    retry_policy: RetryPolicy | None = None,
) -> JobStatus:
    job = _locked_job(session, claimed)
    failed_at = _utc_now(now)
    safe_reason = reason_code if SAFE_REASON.fullmatch(reason_code) else "job_failed"
    job.last_error_code = safe_reason
    job.lease_expires_at = None
    job.lease_token = None
    job.worker_id = None
    if job.attempt_count >= job.max_attempts:
        job.status = JobStatus.DEAD_LETTER
        job.finished_at = failed_at
    else:
        job.status = JobStatus.QUEUED
        policy = retry_policy or RetryPolicy()
        job.run_at = failed_at + policy.delay_for(job.attempt_count)
        job.finished_at = None
    return job.status


def queue_metrics(session: Session, *, now: datetime | None = None) -> QueueMetrics:
    measured_at = _utc_now(now)
    queued_count = (
        session.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.QUEUED))
        or 0
    )
    running_count = (
        session.scalar(select(func.count()).select_from(Job).where(Job.status == JobStatus.RUNNING))
        or 0
    )
    dead_letter_count = (
        session.scalar(
            select(func.count()).select_from(Job).where(Job.status == JobStatus.DEAD_LETTER)
        )
        or 0
    )
    oldest_due = session.scalar(
        select(func.min(Job.run_at)).where(
            Job.status == JobStatus.QUEUED, Job.run_at <= measured_at
        )
    )
    age = max(0.0, (measured_at - oldest_due).total_seconds()) if oldest_due else 0.0
    return QueueMetrics(queued_count, running_count, dead_letter_count, age)


def job_status(session: Session, *, task: str, idempotency_key: str) -> JobStatus | None:
    return session.scalar(
        select(Job.status).where(Job.task == task, Job.idempotency_key == idempotency_key)
    )


def dead_letters(session: Session, *, limit: int = 100) -> tuple[DeadLetter, ...]:
    """Operator-safe dead-letter visibility; payload is deliberately excluded."""
    if not 1 <= limit <= 1000:
        raise JobQueueError("dead_letter_limit_invalid")
    rows = session.scalars(
        select(Job)
        .where(Job.status == JobStatus.DEAD_LETTER)
        .order_by(Job.finished_at.desc(), Job.id)
        .limit(limit)
    )
    return tuple(
        DeadLetter(
            id=row.id,
            task=row.task,
            attempt_count=row.attempt_count,
            max_attempts=row.max_attempts,
            last_error_code=row.last_error_code,
            finished_at=row.finished_at,
        )
        for row in rows
    )
