"""ADR-0004 queue semantics against real PostgreSQL transactions and row locks."""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.config import Settings
from healthcurve.db import SCHEMAS, Base
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.connect_jobs import (
    GARMIN_SYNC_TASK,
    GarminSyncDisposition,
    enqueue_sync,
    make_garmin_handler,
    schedule_garmin_sync,
)
from healthcurve.integrations.garmin.models import (
    GarminConnection,
    GarminConnectionState,
    GarminMetricEvent,
    GarminMetricType,
    GarminSyncOrigin,
    GarminSyncRun,
)
from healthcurve.integrations.telegram.confirmation_reminders import (
    CONFIRMATION_REMINDER_DELAY,
    CONFIRMATION_REMINDER_TASK,
    REMINDER_TEXT,
    make_confirmation_reminder_handler,
    schedule_confirmation_reminder,
)
from healthcurve.integrations.telegram.draft_jobs import (
    DRAFT_EXPIRY_TASK,
    draft_expiry_health,
    make_draft_expiry_handler,
    schedule_draft_expiry,
)
from healthcurve.operations.backup_jobs import BACKUP_TASK, backup_health, schedule_nightly
from healthcurve.operations.jobs import (
    Job,
    JobQueueError,
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


class _SyntheticTelegramClient:
    def __init__(self, *, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.messages: list[tuple[int, str]] = []

    def send_message(
        self,
        chat_id: int,
        text: str,
        *,
        reply_markup: dict[str, Any] | None = None,
    ) -> bool:
        del reply_markup
        self.messages.append((chat_id, text))
        return self.succeeds


class _SyntheticGarminClient:
    def __init__(self) -> None:
        self.login_count = 0

    def login(self) -> None:
        self.login_count += 1

    def get_stats(self, day: str) -> dict[str, Any]:
        del day
        return {"totalSteps": 1_234}

    def get_sleep_data(self, day: str) -> dict[str, Any]:
        del day
        return {}

    def get_heart_rates(self, day: str) -> dict[str, Any]:
        del day
        return {}

    def get_stress_data(self, day: str) -> dict[str, Any]:
        del day
        return {}

    def get_respiration_data(self, day: str) -> dict[str, Any]:
        del day
        return {}

    def get_hrv_data(self, day: str) -> dict[str, Any]:
        del day
        return {}

    def get_steps_data(self, day: str) -> list[dict[str, Any]]:
        del day
        return []

    def get_activities_by_date(self, start: str, end: str) -> list[dict[str, Any]]:
        del start, end
        return []

    def logout(self) -> None:
        return None


class _SyntheticStepGarminClient(_SyntheticGarminClient):
    def get_steps_data(self, day: str) -> list[dict[str, Any]]:
        return [
            {
                "startGMT": f"{day}T12:00:00Z",
                "endGMT": f"{day}T12:15:00Z",
                "steps": 125,
            },
            {
                "startGMT": f"{day}T12:15:00Z",
                "endGMT": f"{day}T12:30:00Z",
                "steps": 75,
            },
        ]


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
        session.query(ExtractionDraft).delete()
        session.query(Job).delete()
    yield maker


def _connected_garmin_owner(
    factory: sessionmaker[Session],
    *,
    email: str,
    timezone: str = "UTC",
    sync_lookback_days: int = 3,
) -> uuid.UUID:
    with factory() as session, session.begin():
        owner = Owner(
            email=email,
            password_hash=hashlib.sha256(email.encode("utf-8")).hexdigest(),
            default_timezone=timezone,
        )
        session.add(owner)
        session.flush()
        session.add(
            GarminConnection(
                owner_id=owner.id,
                state=GarminConnectionState.CONNECTED,
                connected_at=NOW,
                sync_lookback_days=sync_lookback_days,
                capabilities={},
                client_version="synthetic",
            )
        )
        return owner.id


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


def _confirmation_draft(
    factory: sessionmaker[Session],
    *,
    state: DraftState = DraftState.PENDING,
) -> uuid.UUID:
    with factory() as session, session.begin():
        draft = ExtractionDraft(
            owner_id=uuid.uuid4(),
            source="telegram",
            provider_message_id=f"synthetic-confirmation-{uuid.uuid4()}",
            raw_text="SYNTHETIC_TEST_DATA",
            candidates=[],
            state=state,
            prompt_version="test-v1",
            schema_version="test-v1",
            created_at=NOW,
            resolved_at=NOW if state not in {DraftState.PENDING, DraftState.EDITED} else None,
        )
        session.add(draft)
        session.flush()
        return draft.id


def test_confirmation_reminder_waits_one_minute_and_sends_exactly_once(
    factory: sessionmaker[Session],
) -> None:
    draft_id = _confirmation_draft(factory)
    with factory() as session, session.begin():
        first = schedule_confirmation_reminder(
            session,
            draft_id=draft_id,
            confirmation_sent_at=NOW,
        )
        duplicate = schedule_confirmation_reminder(
            session,
            draft_id=draft_id,
            confirmation_sent_at=NOW + timedelta(seconds=10),
        )
        assert duplicate.id == first.id
        assert first.run_at == NOW + CONFIRMATION_REMINDER_DELAY
        assert first.payload == {"draft_id": str(draft_id)}

    with factory() as session, session.begin():
        assert (
            claim(
                session,
                worker_id="confirmation-reminder-early",
                now=NOW + CONFIRMATION_REMINDER_DELAY - timedelta(microseconds=1),
                tasks={CONFIRMATION_REMINDER_TASK},
            )
            is None
        )

    client = _SyntheticTelegramClient()
    claimed = run_once(
        factory,
        {
            CONFIRMATION_REMINDER_TASK: make_confirmation_reminder_handler(
                client=client,  # type: ignore[arg-type]
                chat_id=4242,
            )
        },
        worker_id="confirmation-reminder-test",
    )
    assert claimed is not None and claimed.id == first.id
    assert client.messages == [(4242, REMINDER_TEXT)]
    assert "SYNTHETIC_TEST_DATA" not in REMINDER_TEXT
    assert (
        run_once(
            factory,
            {
                CONFIRMATION_REMINDER_TASK: make_confirmation_reminder_handler(
                    client=client,  # type: ignore[arg-type]
                    chat_id=4242,
                )
            },
            worker_id="confirmation-reminder-test",
        )
        is None
    )
    assert client.messages == [(4242, REMINDER_TEXT)]


@pytest.mark.parametrize(
    "state",
    [DraftState.CONFIRMED, DraftState.CANCELLED, DraftState.EXPIRED],
)
def test_confirmation_reminder_is_suppressed_after_draft_resolution(
    factory: sessionmaker[Session],
    state: DraftState,
) -> None:
    draft_id = _confirmation_draft(factory, state=state)
    with factory() as session, session.begin():
        scheduled = schedule_confirmation_reminder(
            session,
            draft_id=draft_id,
            confirmation_sent_at=NOW,
        )

    client = _SyntheticTelegramClient()
    claimed = run_once(
        factory,
        {
            CONFIRMATION_REMINDER_TASK: make_confirmation_reminder_handler(
                client=client,  # type: ignore[arg-type]
                chat_id=4242,
            )
        },
        worker_id="confirmation-reminder-resolved-test",
    )
    assert claimed is not None and claimed.id == scheduled.id
    assert client.messages == []
    with factory() as session:
        stored = session.get(Job, scheduled.id)
        assert stored is not None and stored.status is JobStatus.COMPLETED


def test_confirmation_reminder_send_failure_is_retried(
    factory: sessionmaker[Session],
) -> None:
    draft_id = _confirmation_draft(factory)
    with factory() as session, session.begin():
        scheduled = schedule_confirmation_reminder(
            session,
            draft_id=draft_id,
            confirmation_sent_at=NOW,
        )

    client = _SyntheticTelegramClient(succeeds=False)
    claimed = run_once(
        factory,
        {
            CONFIRMATION_REMINDER_TASK: make_confirmation_reminder_handler(
                client=client,  # type: ignore[arg-type]
                chat_id=4242,
            )
        },
        worker_id="confirmation-reminder-failure-test",
    )
    assert claimed is not None and claimed.id == scheduled.id
    with factory() as session:
        stored = session.get(Job, scheduled.id)
        assert stored is not None and stored.status is JobStatus.QUEUED
        assert stored.attempt_count == 1
        assert stored.last_error_code == "telegram_confirmation_reminder_send_failed"


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


def test_garmin_manual_and_scheduler_restart_share_one_provider_fetch(
    factory: sessionmaker[Session],
) -> None:
    owner_id = _connected_garmin_owner(
        factory, email="garmin-coalescing@example.test", sync_lookback_days=7
    )
    settings = Settings.model_validate({"garmin_enabled": True})
    client = _SyntheticGarminClient()
    initial_start = NOW.date() - timedelta(days=6)

    with factory() as session, session.begin():
        manual = enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=initial_start,
            end_date=NOW.date(),
            timezone="UTC",
            idempotency_key=f"manual:{owner_id}:synthetic-a",
            now=NOW,
        )
        assert manual.disposition is GarminSyncDisposition.QUEUED
    for _restart in range(2):
        with factory() as session, session.begin():
            schedule_garmin_sync(session, NOW, settings=settings)

    with factory() as session:
        jobs = list(session.scalars(select(Job).where(Job.task == GARMIN_SYNC_TASK)))
        assert len(jobs) == 1
        assert jobs[0].id == manual.job.id

    claimed = run_once(
        factory,
        {GARMIN_SYNC_TASK: make_garmin_handler(settings, client_factory=lambda: client)},
        worker_id="garmin-coalescing-test",
    )
    assert claimed is not None and claimed.id == manual.job.id
    assert (
        run_once(
            factory,
            {GARMIN_SYNC_TASK: make_garmin_handler(settings, client_factory=lambda: client)},
            worker_id="garmin-coalescing-test",
        )
        is None
    )
    assert client.login_count == 1

    with factory() as session:
        run = session.scalar(select(GarminSyncRun))
        assert run is not None and run.origin is GarminSyncOrigin.MANUAL
        stored = session.get(Job, manual.job.id)
        assert stored is not None and stored.status is JobStatus.COMPLETED

    # The completed provider read covers today's saved owner window, so the scheduler
    # must not perform a second same-day read after the manual cooldown expires.
    with factory() as session, session.begin():
        schedule_garmin_sync(session, NOW + timedelta(minutes=31), settings=settings)
    with factory() as session:
        jobs = list(session.scalars(select(Job).where(Job.task == GARMIN_SYNC_TASK)))
        assert len(jobs) == 1

    next_day = NOW + timedelta(days=1)
    with factory() as session, session.begin():
        schedule_garmin_sync(session, next_day, settings=settings)
    with factory() as session:
        windows = {
            (job.payload["start_date"], job.payload["end_date"])
            for job in session.scalars(select(Job).where(Job.task == GARMIN_SYNC_TASK))
        }
    assert windows == {
        (initial_start.isoformat(), NOW.date().isoformat()),
        ((next_day.date() - timedelta(days=6)).isoformat(), next_day.date().isoformat()),
    }
    with factory() as session:
        scheduled = session.scalar(
            select(Job).where(Job.idempotency_key == f"scheduled:{owner_id}:{next_day.date()}")
        )
        assert scheduled is not None
        assert scheduled.payload["origin"] == GarminSyncOrigin.SCHEDULED.value


def test_garmin_scheduler_waits_until_configured_owner_local_hour_across_dst(
    factory: sessionmaker[Session],
) -> None:
    owner_id = _connected_garmin_owner(
        factory,
        email="garmin-local-hour@example.test",
        timezone="America/New_York",
    )
    settings = Settings.model_validate({"garmin_enabled": True, "garmin_sync_hour_local": 9})
    # 2026-03-08 is the spring DST transition: 12:59 UTC is 08:59 EDT and
    # 13:00 UTC is 09:00 EDT. The gate follows the owner's wall clock.
    before = datetime(2026, 3, 8, 12, 59, tzinfo=UTC)
    at_hour = datetime(2026, 3, 8, 13, 0, tzinfo=UTC)

    with factory() as session, session.begin():
        schedule_garmin_sync(session, before, settings=settings)
    with factory() as session:
        scheduled_key = f"scheduled:{owner_id}:2026-03-08"
        assert session.scalar(select(Job).where(Job.idempotency_key == scheduled_key)) is None

    with factory() as session, session.begin():
        schedule_garmin_sync(session, at_hour, settings=settings)
    with factory() as session:
        scheduled = session.scalar(select(Job).where(Job.idempotency_key == scheduled_key))
        assert scheduled is not None
        assert scheduled.payload["timezone"] == "America/New_York"


@pytest.mark.parametrize(
    ("origin", "force_refresh", "expected"),
    [
        (GarminSyncOrigin.SCHEDULED, False, GarminSyncOrigin.SCHEDULED),
        (GarminSyncOrigin.MANUAL_REFRESH, True, GarminSyncOrigin.MANUAL_REFRESH),
    ],
)
def test_garmin_worker_persists_explicit_sync_origin(
    factory: sessionmaker[Session],
    origin: GarminSyncOrigin,
    force_refresh: bool,
    expected: GarminSyncOrigin,
) -> None:
    owner_id = _connected_garmin_owner(factory, email=f"garmin-origin-{origin.value}@example.test")
    settings = Settings.model_validate({"garmin_enabled": True})
    with factory() as session, session.begin():
        result = enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=NOW.date(),
            end_date=NOW.date(),
            timezone="UTC",
            idempotency_key=f"origin:{owner_id}:{origin.value}",
            force_refresh=force_refresh,
            origin=origin,
            now=NOW,
        )
        assert result.job.payload["origin"] == expected.value

    claimed = run_once(
        factory,
        {GARMIN_SYNC_TASK: make_garmin_handler(settings, client_factory=_SyntheticGarminClient)},
        worker_id=f"garmin-origin-{origin.value}",
    )
    assert claimed is not None
    with factory() as session:
        run = session.scalar(select(GarminSyncRun).where(GarminSyncRun.owner_id == owner_id))
        assert run is not None and run.origin is expected


def test_garmin_worker_persists_observed_hourly_steps(
    factory: sessionmaker[Session],
) -> None:
    owner_id = _connected_garmin_owner(factory, email="garmin-hourly-steps@example.test")
    settings = Settings.model_validate({"garmin_enabled": True})
    with factory() as session, session.begin():
        enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=NOW.date(),
            end_date=NOW.date(),
            timezone="UTC",
            idempotency_key=f"hourly-steps:{owner_id}",
            now=NOW,
        )

    claimed = run_once(
        factory,
        {
            GARMIN_SYNC_TASK: make_garmin_handler(
                settings, client_factory=_SyntheticStepGarminClient
            )
        },
        worker_id="garmin-hourly-steps",
    )
    assert claimed is not None
    with factory() as session:
        step = session.scalar(
            select(GarminMetricEvent).where(
                GarminMetricEvent.owner_id == owner_id,
                GarminMetricEvent.metric_type == GarminMetricType.STEPS,
                GarminMetricEvent.garmin_field_name == "hourlySteps",
            )
        )
        assert step is not None
        assert step.value == 200
        assert step.unit == "steps"
        assert step.sample_interval_seconds == 3_600


def test_garmin_worker_accepts_legacy_payload_and_marks_run_origin(
    factory: sessionmaker[Session],
) -> None:
    owner_id = _connected_garmin_owner(factory, email="garmin-legacy-origin@example.test")
    settings = Settings.model_validate({"garmin_enabled": True})
    with factory() as session, session.begin():
        enqueue(
            session,
            task=GARMIN_SYNC_TASK,
            payload={
                "owner_id": str(owner_id),
                "start_date": NOW.date().isoformat(),
                "end_date": NOW.date().isoformat(),
                "timezone": "UTC",
            },
            idempotency_key=f"legacy:{owner_id}",
            run_at=NOW,
        )

    claimed = run_once(
        factory,
        {GARMIN_SYNC_TASK: make_garmin_handler(settings, client_factory=_SyntheticGarminClient)},
        worker_id="garmin-legacy-origin",
    )
    assert claimed is not None
    with factory() as session:
        run = session.scalar(select(GarminSyncRun).where(GarminSyncRun.owner_id == owner_id))
        assert run is not None and run.origin is GarminSyncOrigin.LEGACY


def test_concurrent_garmin_requests_with_different_keys_coalesce(
    factory: sessionmaker[Session],
) -> None:
    owner_id = _connected_garmin_owner(factory, email="garmin-concurrent@example.test")
    second_started = Event()

    def enqueue_second() -> tuple[uuid.UUID, GarminSyncDisposition]:
        with factory() as session, session.begin():
            second_started.set()
            result = enqueue_sync(
                session,
                owner_id=owner_id,
                start_date=NOW.date(),
                end_date=NOW.date(),
                timezone="UTC",
                idempotency_key=f"manual:{owner_id}:concurrent-b",
                now=NOW,
            )
            return result.job.id, result.disposition

    with ThreadPoolExecutor(max_workers=1) as executor:
        with factory() as first_session, first_session.begin():
            first = enqueue_sync(
                first_session,
                owner_id=owner_id,
                start_date=NOW.date(),
                end_date=NOW.date(),
                timezone="UTC",
                idempotency_key=f"manual:{owner_id}:concurrent-a",
                now=NOW,
            )
            future = executor.submit(enqueue_second)
            assert second_started.wait(timeout=5)
            assert not future.done()
        second_id, second_disposition = future.result(timeout=5)

    assert second_id == first.job.id
    assert second_disposition is GarminSyncDisposition.COALESCED_ACTIVE
    with factory() as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.task == GARMIN_SYNC_TASK)
            )
            == 1
        )

    with factory() as session, session.begin():
        claimed = claim(session, worker_id="garmin-running", now=NOW, tasks={GARMIN_SYNC_TASK})
        assert claimed is not None
    lock_session = factory()
    lock_transaction = lock_session.begin()
    executor = ThreadPoolExecutor(max_workers=1)
    try:
        locked = lock_session.scalar(
            select(GarminConnection).where(GarminConnection.owner_id == owner_id).with_for_update()
        )
        assert locked is not None
        running_duplicate = executor.submit(enqueue_second).result(timeout=2)
        assert running_duplicate == (first.job.id, GarminSyncDisposition.COALESCED_ACTIVE)
    finally:
        lock_transaction.rollback()
        lock_session.close()
        executor.shutdown(wait=True)


def test_garmin_completed_cooldown_refresh_and_distinct_windows_are_deterministic(
    factory: sessionmaker[Session],
) -> None:
    owner_id = _connected_garmin_owner(factory, email="garmin-cooldown@example.test")
    with factory() as session, session.begin():
        first = enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=NOW.date(),
            end_date=NOW.date(),
            timezone="UTC",
            idempotency_key=f"manual:{owner_id}:cooldown-a",
            now=NOW,
        )
    with factory() as session, session.begin():
        claimed = claim(session, worker_id="garmin-cooldown", now=NOW, tasks={GARMIN_SYNC_TASK})
        assert claimed is not None
        complete(session, claimed, now=NOW + timedelta(seconds=1))

    with factory() as session, session.begin():
        cooled = enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=NOW.date(),
            end_date=NOW.date(),
            timezone="UTC",
            idempotency_key=f"manual:{owner_id}:cooldown-b",
            origin=GarminSyncOrigin.SCHEDULED,
            now=NOW + timedelta(minutes=1),
        )
        assert cooled.job.id == first.job.id
        assert cooled.disposition is GarminSyncDisposition.COOLDOWN_REUSED
        assert cooled.cooldown_until == NOW + timedelta(minutes=30, seconds=1)

    after_cooldown = NOW + timedelta(minutes=31)
    with factory() as session, session.begin():
        next_job = enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=NOW.date(),
            end_date=NOW.date(),
            timezone="UTC",
            idempotency_key=f"manual:{owner_id}:cooldown-c",
            now=after_cooldown,
        )
        assert next_job.job.id != first.job.id
        assert next_job.disposition is GarminSyncDisposition.QUEUED
    with factory() as session, session.begin():
        claimed = claim(
            session,
            worker_id="garmin-cooldown",
            now=after_cooldown,
            tasks={GARMIN_SYNC_TASK},
        )
        assert claimed is not None
        complete(session, claimed, now=after_cooldown + timedelta(seconds=1))

    with factory() as session, session.begin():
        refresh = enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=NOW.date(),
            end_date=NOW.date(),
            timezone="UTC",
            idempotency_key=f"manual:{owner_id}:refresh",
            force_refresh=True,
            now=after_cooldown + timedelta(minutes=1),
        )
        replay = enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=NOW.date(),
            end_date=NOW.date(),
            timezone="UTC",
            idempotency_key=f"manual:{owner_id}:refresh-replay",
            force_refresh=True,
            now=after_cooldown + timedelta(minutes=1),
        )
        distinct = enqueue_sync(
            session,
            owner_id=owner_id,
            start_date=NOW.date() + timedelta(days=1),
            end_date=NOW.date() + timedelta(days=1),
            timezone="UTC",
            idempotency_key=f"manual:{owner_id}:different-window",
            now=after_cooldown + timedelta(minutes=1),
        )

    assert refresh.disposition is GarminSyncDisposition.REFRESH_QUEUED
    assert refresh.job.payload["origin"] == GarminSyncOrigin.MANUAL_REFRESH.value
    assert replay.job.id == refresh.job.id
    assert replay.disposition is GarminSyncDisposition.COALESCED_ACTIVE
    assert distinct.job.id != refresh.job.id
    assert distinct.disposition is GarminSyncDisposition.QUEUED


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


def test_draft_expiry_schedule_is_idempotent_per_time_bucket(
    factory: sessionmaker[Session],
) -> None:
    first_time = datetime(2026, 8, 9, 12, 2, tzinfo=UTC)
    same_bucket = datetime(2026, 8, 9, 12, 14, 59, tzinfo=UTC)
    next_bucket = datetime(2026, 8, 9, 12, 15, tzinfo=UTC)
    with factory() as session, session.begin():
        first = schedule_draft_expiry(session, first_time)
        duplicate = schedule_draft_expiry(session, same_bucket)
        following = schedule_draft_expiry(session, next_bucket)
        assert duplicate.id == first.id
        assert following.id != first.id
        assert first.run_at == datetime(2026, 8, 9, 12, tzinfo=UTC)
        assert first.payload == {"scheduled_at_utc": "2026-08-09T12:00:00Z"}


def test_draft_expiry_schedule_rejects_naive_time(
    factory: sessionmaker[Session],
) -> None:
    with (
        factory() as session,
        session.begin(),
        pytest.raises(JobQueueError, match="draft_expiry_schedule_invalid"),
    ):
        schedule_draft_expiry(session, datetime(2026, 8, 9, 12))  # noqa: DTZ001


def test_worker_expires_only_stale_drafts_and_purges_raw_text(
    factory: sessionmaker[Session],
) -> None:
    measured_at = datetime.now(UTC)
    owner_id = uuid.uuid4()
    with factory() as session, session.begin():
        stale = ExtractionDraft(
            owner_id=owner_id,
            source="telegram",
            provider_message_id="synthetic-expiry-stale",
            raw_text="SYNTHETIC_TEST_DATA stale draft",
            candidates=[],
            prompt_version="test-v1",
            schema_version="test-v1",
            created_at=measured_at - timedelta(hours=7),
        )
        fresh = ExtractionDraft(
            owner_id=owner_id,
            source="telegram",
            provider_message_id="synthetic-expiry-fresh",
            raw_text="SYNTHETIC_TEST_DATA fresh draft",
            candidates=[],
            prompt_version="test-v1",
            schema_version="test-v1",
            created_at=measured_at - timedelta(hours=5),
        )
        session.add_all((stale, fresh))
        session.flush()
        stale_id, fresh_id = stale.id, fresh.id
        scheduled = schedule_draft_expiry(session, measured_at)

    claimed = run_once(
        factory,
        {DRAFT_EXPIRY_TASK: make_draft_expiry_handler(clock=lambda: measured_at)},
        worker_id="draft-expiry-test",
    )
    assert claimed is not None and claimed.id == scheduled.id

    with factory() as session:
        expired = session.get(ExtractionDraft, stale_id)
        retained = session.get(ExtractionDraft, fresh_id)
        health = draft_expiry_health(session)
        assert expired is not None and expired.state is DraftState.EXPIRED
        assert expired.raw_text is None
        assert expired.resolved_at == measured_at
        assert retained is not None and retained.state is DraftState.PENDING
        assert retained.raw_text == "SYNTHETIC_TEST_DATA fresh draft"
        assert health.latest_job_status is JobStatus.COMPLETED
        assert health.started_at is not None
        assert health.finished_at is not None
        assert health.latest_job_error_code is None


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
