"""ADR-0004 queue semantics against real PostgreSQL transactions and row locks."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.db import SCHEMAS, Base
from healthcurve.operations.backup_jobs import BACKUP_TASK, backup_health, schedule_nightly
from healthcurve.operations.jobs import (
    Job,
    JobStatus,
    claim,
    complete,
    dead_letters,
    enqueue,
    fail,
    job_status,
    queue_metrics,
)
from healthcurve.operations.worker import run_once

pytestmark = [pytest.mark.postgres, pytest.mark.slow]
NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        eng = create_engine(container.get_connection_url())
        with eng.begin() as connection:
            for schema in SCHEMAS:
                connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        Base.metadata.create_all(eng)
        yield eng
        eng.dispose()


@pytest.fixture
def factory(engine: Engine) -> Iterator[sessionmaker[Session]]:
    maker = sessionmaker(engine, expire_on_commit=False)
    with maker() as session, session.begin():
        session.query(Job).delete()
    yield maker


def test_enqueue_uses_the_callers_transaction(factory: sessionmaker[Session]) -> None:
    with factory() as session:
        transaction = session.begin()
        job = enqueue(
            session,
            task="synthetic.import",
            payload={"record_id": "opaque-1"},
            idempotency_key="rolled-back",
            run_at=NOW,
        )
        identifier = job.id
        transaction.rollback()
    with factory() as session:
        assert session.get(Job, identifier) is None


def test_completed_idempotency_key_is_a_no_op(factory: sessionmaker[Session]) -> None:
    with factory() as session, session.begin():
        first = enqueue(
            session,
            task="synthetic.import",
            payload={"version": 1},
            idempotency_key="same-source",
            run_at=NOW,
        )
        identifier = first.id
    with factory() as session, session.begin():
        claimed = claim(session, worker_id="worker-a", now=NOW)
        assert claimed is not None
    with factory() as session, session.begin():
        complete(session, claimed, now=NOW + timedelta(seconds=1))
    with factory() as session, session.begin():
        duplicate = enqueue(
            session,
            task="synthetic.import",
            payload={"version": 2},
            idempotency_key="same-source",
            run_at=NOW,
        )
        assert duplicate.id == identifier
        assert duplicate.status is JobStatus.COMPLETED
        assert duplicate.payload == {"version": 1}


def test_skip_locked_prevents_two_workers_claiming_one_job(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session, session.begin():
        enqueue(
            session,
            task="synthetic.contended",
            payload={},
            idempotency_key="one",
            run_at=NOW,
        )
    first_session = factory()
    second_session = factory()
    first_transaction = first_session.begin()
    second_transaction = second_session.begin()
    try:
        first = claim(first_session, worker_id="worker-a", now=NOW)
        second = claim(second_session, worker_id="worker-b", now=NOW)
        assert first is not None
        assert second is None
    finally:
        second_transaction.rollback()
        first_transaction.rollback()
        second_session.close()
        first_session.close()


def test_scheduled_job_waits_until_due(factory: sessionmaker[Session]) -> None:
    due = NOW + timedelta(hours=2)
    with factory() as session, session.begin():
        enqueue(
            session,
            task="synthetic.scheduled",
            payload={},
            idempotency_key="nightly",
            run_at=due,
        )
    with factory() as session, session.begin():
        assert claim(session, worker_id="worker-a", now=due - timedelta(microseconds=1)) is None
    with factory() as session, session.begin():
        assert claim(session, worker_id="worker-a", now=due) is not None


def test_bounded_backoff_ends_in_visible_dead_letter(factory: sessionmaker[Session]) -> None:
    with factory() as session, session.begin():
        job = enqueue(
            session,
            task="synthetic.retry",
            payload={},
            idempotency_key="bounded",
            run_at=NOW,
            max_attempts=2,
        )
        identifier = job.id
    with factory() as session, session.begin():
        first = claim(session, worker_id="worker-a", now=NOW)
        assert first is not None and first.attempt == 1
    with factory() as session, session.begin():
        assert (
            fail(session, first, reason_code="unsafe details: private", now=NOW) is JobStatus.QUEUED
        )
    with factory() as session:
        stored = session.get(Job, identifier)
        assert stored is not None
        assert stored.last_error_code == "job_failed"
        assert stored.run_at == NOW + timedelta(seconds=30)
    with factory() as session, session.begin():
        assert claim(session, worker_id="worker-a", now=NOW + timedelta(seconds=29)) is None
    with factory() as session, session.begin():
        second = claim(session, worker_id="worker-b", now=NOW + timedelta(seconds=30))
        assert second is not None and second.attempt == 2
    with factory() as session, session.begin():
        status = fail(
            session,
            second,
            reason_code="provider_unavailable",
            now=NOW + timedelta(seconds=30),
        )
        assert status is JobStatus.DEAD_LETTER
    with factory() as session:
        metrics = queue_metrics(session, now=NOW + timedelta(minutes=1))
        stored = session.get(Job, identifier)
        assert metrics.dead_letter_count == 1
        assert stored is not None and stored.status is JobStatus.DEAD_LETTER
        assert stored.last_error_code == "provider_unavailable"
        assert job_status(session, task="synthetic.retry", idempotency_key="bounded") is (
            JobStatus.DEAD_LETTER
        )
        visible = dead_letters(session)
        assert len(visible) == 1
        assert visible[0].id == identifier
        assert visible[0].last_error_code == "provider_unavailable"


def test_expired_final_lease_is_dead_lettered(factory: sessionmaker[Session]) -> None:
    with factory() as session, session.begin():
        enqueue(
            session,
            task="synthetic.crash",
            payload={},
            idempotency_key="crashed-final-attempt",
            run_at=NOW,
            max_attempts=1,
        )
    with factory() as session, session.begin():
        claimed = claim(
            session,
            worker_id="worker-a",
            now=NOW,
            lease_duration=timedelta(seconds=10),
        )
        assert claimed is not None
    with factory() as session, session.begin():
        assert claim(session, worker_id="worker-b", now=NOW + timedelta(seconds=10)) is None
    with factory() as session:
        assert queue_metrics(session, now=NOW + timedelta(seconds=10)).dead_letter_count == 1


def test_metrics_expose_due_age_and_counts(factory: sessionmaker[Session]) -> None:
    with factory() as session, session.begin():
        enqueue(
            session,
            task="synthetic.metrics",
            payload={},
            idempotency_key="oldest",
            run_at=NOW - timedelta(seconds=90),
        )
        enqueue(
            session,
            task="synthetic.metrics",
            payload={},
            idempotency_key="future",
            run_at=NOW + timedelta(hours=1),
        )
    with factory() as session:
        metrics = queue_metrics(session, now=NOW)
    assert metrics.queued_count == 2
    assert metrics.running_count == 0
    assert metrics.dead_letter_count == 0
    assert metrics.oldest_due_age_seconds == 90


def test_worker_rolls_back_handler_writes_before_recording_failure(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session, session.begin():
        parent = enqueue(
            session,
            task="synthetic.handler",
            payload={},
            idempotency_key="parent",
            run_at=datetime.now(UTC) - timedelta(seconds=1),
            max_attempts=1,
        )
        parent_id = parent.id

    def failing_handler(session: Session, _payload: object) -> None:
        enqueue(
            session,
            task="synthetic.child",
            payload={},
            idempotency_key="must-roll-back",
        )
        raise RuntimeError("synthetic private detail")

    claimed = run_once(factory, {"synthetic.handler": failing_handler}, worker_id="worker-a")
    assert claimed is not None and claimed.id == parent_id
    with factory() as session:
        parent = session.get(Job, parent_id)
        child = session.scalar(
            select(Job).where(
                Job.task == "synthetic.child", Job.idempotency_key == "must-roll-back"
            )
        )
        assert child is None
        assert parent is not None and parent.status is JobStatus.DEAD_LETTER
        assert parent.last_error_code == "handler_failed"


def test_nightly_schedule_is_singleton_and_task_restricted(
    factory: sessionmaker[Session],
) -> None:
    before_due = datetime(2026, 8, 9, 1, tzinfo=UTC)
    after_due = datetime(2026, 8, 9, 3, tzinfo=UTC)
    with factory() as session, session.begin():
        first = schedule_nightly(session, before_due)
        second = schedule_nightly(session, after_due)
        assert first.id == second.id
        assert first.run_at == datetime(2026, 8, 9, 2, tzinfo=UTC)
    with factory() as session, session.begin():
        assert claim(session, worker_id="general", now=after_due, tasks={"telegram.sync"}) is None
    with factory() as session, session.begin():
        claimed = claim(session, worker_id="backup", now=after_due, tasks={BACKUP_TASK})
        assert claimed is not None and claimed.id == first.id


def _write_valid_set(directory: Path, *, created_at: datetime) -> None:
    set_id = "hc-20260808T120000Z-synthetic"
    archive = directory / f"{set_id}.tar.age"
    archive.write_bytes(b"synthetic encrypted backup")
    envelope = {
        "format_version": 1,
        "set_id": set_id,
        "created_at": created_at.isoformat(),
        "archive": archive.name,
        "size": archive.stat().st_size,
        "sha256": hashlib.sha256(archive.read_bytes()).hexdigest(),
        "verified": True,
    }
    (directory / f"{set_id}.json").write_text(json.dumps(envelope), encoding="utf-8")


def test_backup_health_age_threshold_and_integrity_alert(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    measured_at = datetime(2026, 8, 9, 14, tzinfo=UTC)
    _write_valid_set(tmp_path, created_at=measured_at - timedelta(hours=25))
    with factory() as session:
        healthy = backup_health(session, tmp_path, now=measured_at)
        assert healthy.state == "healthy"
        assert healthy.age_hours == 25
        warning = backup_health(session, tmp_path, now=measured_at + timedelta(hours=1))
        assert warning.state == "alert"
        assert warning.reason_codes == ("backup_age_warning",)

    (tmp_path / "hc-broken.json").write_text("not-json", encoding="utf-8")
    with factory() as session:
        broken = backup_health(session, tmp_path, now=measured_at)
        assert "backup_integrity_failed" in broken.reason_codes
        assert broken.protected_set_count == 1


def test_backup_health_exposes_redacted_dead_letter_status(
    factory: sessionmaker[Session], tmp_path: Path
) -> None:
    with factory() as session, session.begin():
        enqueue(
            session,
            task=BACKUP_TASK,
            payload={},
            idempotency_key="nightly:failed",
            run_at=NOW,
            max_attempts=1,
        )
    with factory() as session, session.begin():
        claimed = claim(session, worker_id="backup", now=NOW, tasks={BACKUP_TASK})
        assert claimed is not None
    with factory() as session, session.begin():
        assert fail(session, claimed, reason_code="encryption_failed", now=NOW) is (
            JobStatus.DEAD_LETTER
        )
    with factory() as session:
        health = backup_health(session, tmp_path, now=NOW)
    assert health.state == "alert"
    assert set(health.reason_codes) == {"backup_missing", "backup_job_failed"}
    assert health.latest_job_error_code == "encryption_failed"
