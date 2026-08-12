"""Scheduled-dose reminder behavior against PostgreSQL persistence."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.config import Settings
from healthcurve.db import SCHEMAS, Base
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.timekeeping import resolve_event_time
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.dose_reminders import (
    REMINDER_DELAY,
    handle_action,
    schedule_due_reminders,
    send_due_reminders,
)
from healthcurve.integrations.telegram.handlers import cancel_draft, confirm_draft
from healthcurve.integrations.telegram.models import DoseReminderState, TelegramDoseReminder
from healthcurve.medications import service as medications
from healthcurve.medications.models import (
    DoseCategory,
    DoseEvent,
    DoseUnit,
    Medication,
    RegimenDoseSlot,
    Route,
)
from healthcurve.operations.jobs import JobQueueError

pytestmark = [pytest.mark.postgres, pytest.mark.slow]


class _CapturingTelegramClient(TelegramClient):
    def __init__(self) -> None:
        super().__init__(Settings(), token=SecretStr("synthetic-token"))
        self.messages: list[tuple[int | str, str, dict[str, object] | None]] = []

    def send_message(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_markup: dict[str, object] | None = None,
        reply_to_message_id: int | None = None,
    ) -> bool:
        del reply_to_message_id
        self.messages.append((chat_id, text, reply_markup))
        return True


class _FailingTelegramClient(_CapturingTelegramClient):
    def send_message(  # pyright: ignore[reportIncompatibleMethodOverride]
        self,
        chat_id: int | str,
        text: str,
        *,
        reply_markup: dict[str, object] | None = None,
        reply_to_message_id: int | None = None,
    ) -> bool:
        del chat_id, text, reply_markup, reply_to_message_id
        return False


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
def factory(engine: Engine) -> sessionmaker[Session]:
    table_names = ", ".join(
        f'"{table.schema}"."{table.name}"' for table in Base.metadata.sorted_tables
    )
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE TABLE {table_names} CASCADE"))
    return sessionmaker(engine, expire_on_commit=False)


def _approved_plan(
    session: Session,
    *,
    timezone: str = "America/New_York",
    clocks: tuple[time, ...] = (time(7),),
    effective_from: datetime = datetime(2026, 1, 1),  # noqa: DTZ001
) -> tuple[Owner, Medication, list[RegimenDoseSlot]]:
    owner = Owner(
        email=f"reminders-{uuid.uuid4()}@example.invalid",
        display_name="Synthetic reminder owner",
        default_timezone=timezone,
        password_hash="synthetic-not-a-real-password-hash",
    )
    session.add(owner)
    session.flush()
    medication = Medication(
        owner_id=owner.id,
        name="Synthetic hydrocortisone",
        normalized_name=f"synthetic-hydrocortisone-{uuid.uuid4()}",
        default_unit=DoseUnit.MG,
        default_route=Route.ORAL,
    )
    session.add(medication)
    session.flush()
    version = medications.create_draft(
        session,
        owner_id=owner.id,
        version_label="Synthetic approved reminder plan",
        effective_from=effective_from,
        effective_timezone=timezone,
    )
    slots = [
        RegimenDoseSlot(
            regimen_version_id=version.id,
            medication_id=medication.id,
            scheduled_local_time=clock,
            amount=Decimal("10"),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            sort_order=index,
        )
        for index, clock in enumerate(clocks)
    ]
    session.add_all(slots)
    session.flush()
    medications.approve_version(
        session,
        version,
        approved_by="Synthetic clinician",
        approval_source="Synthetic test plan",
        approved_at=datetime(2026, 1, 1, tzinfo=UTC),
    )
    return owner, medication, slots


def _record_dose(
    session: Session,
    owner: Owner,
    medication: Medication,
    *,
    local_time: datetime,
    category: DoseCategory,
) -> DoseEvent:
    return events.create_event(
        session,
        DoseEvent,
        owner_id=owner.id,
        event_time=resolve_event_time(local_time, owner.default_timezone),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        medication_id=medication.id,
        amount=Decimal("10"),
        unit=DoseUnit.MG,
        route=Route.ORAL,
        category=category,
    )


def test_one_reminder_at_thirty_minutes_and_restart_idempotency(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session, session.begin():
        owner, _, slots = _approved_plan(session)
        before_due = datetime(2026, 8, 12, 11, 29, tzinfo=UTC)
        assert schedule_due_reminders(session, before_due) == 0
        due = before_due + timedelta(minutes=1)
        assert schedule_due_reminders(session, due) == 1
        assert schedule_due_reminders(session, due) == 0
        reminder = session.scalar(
            select(TelegramDoseReminder).where(TelegramDoseReminder.owner_id == owner.id)
        )
        assert reminder is not None
        assert reminder.slot_id == slots[0].id
        assert reminder.due_at == reminder.scheduled_at + REMINDER_DELAY

        client = _CapturingTelegramClient()
        assert send_due_reminders(session, client=client, chat_id=123, now=due) == 1
        assert send_due_reminders(session, client=client, chat_id=123, now=due) == 0
        assert len(client.messages) == 1
        assert "appears unrecorded" in client.messages[0][1]
        assert "not advice to take medication" in client.messages[0][1]


@pytest.mark.parametrize(
    ("category", "expected"),
    ((DoseCategory.SCHEDULED, 0), (DoseCategory.STRESS, 1)),
)
def test_only_a_regular_dose_suppresses_the_reminder(
    factory: sessionmaker[Session], category: DoseCategory, expected: int
) -> None:
    with factory() as session, session.begin():
        owner, medication, _ = _approved_plan(session)
        _record_dose(
            session,
            owner,
            medication,
            local_time=datetime(2026, 8, 12, 7, 5),  # noqa: DTZ001
            category=category,
        )
        assert (
            schedule_due_reminders(session, datetime(2026, 8, 12, 11, 30, tzinfo=UTC)) == expected
        )


def test_late_fact_satisfies_a_pending_reminder_without_sending(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session, session.begin():
        owner, medication, _ = _approved_plan(session)
        now = datetime(2026, 8, 12, 11, 30, tzinfo=UTC)
        assert schedule_due_reminders(session, now) == 1
        _record_dose(
            session,
            owner,
            medication,
            local_time=datetime(2026, 8, 12, 7, 10),  # noqa: DTZ001
            category=DoseCategory.SCHEDULED,
        )
        client = _CapturingTelegramClient()
        assert send_due_reminders(session, client=client, chat_id=123, now=now) == 0
        reminder = session.scalar(
            select(TelegramDoseReminder).where(TelegramDoseReminder.owner_id == owner.id)
        )
        assert reminder is not None and reminder.state is DoseReminderState.SATISFIED
        assert client.messages == []


def test_failed_delivery_remains_retryable(factory: sessionmaker[Session]) -> None:
    with factory() as session, session.begin():
        owner, _, _ = _approved_plan(session)
        now = datetime(2026, 8, 12, 11, 30, tzinfo=UTC)
        schedule_due_reminders(session, now)
        with pytest.raises(JobQueueError, match="telegram_dose_reminder_send_failed"):
            send_due_reminders(session, client=_FailingTelegramClient(), chat_id=123, now=now)
        reminder = session.scalar(
            select(TelegramDoseReminder).where(TelegramDoseReminder.owner_id == owner.id)
        )
        assert reminder is not None and reminder.state is DoseReminderState.PENDING
        client = _CapturingTelegramClient()
        assert send_due_reminders(session, client=client, chat_id=123, now=now) == 1


def test_multiple_slots_are_scheduled_independently(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session, session.begin():
        owner, _, _ = _approved_plan(session, clocks=(time(7), time(8)))
        assert schedule_due_reminders(session, datetime(2026, 8, 12, 11, 31, tzinfo=UTC)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramDoseReminder)
                .where(TelegramDoseReminder.owner_id == owner.id)
            )
            == 1
        )
        assert schedule_due_reminders(session, datetime(2026, 8, 12, 12, 30, tzinfo=UTC)) == 1


def test_dst_uses_the_plan_timezone_offset(factory: sessionmaker[Session]) -> None:
    with factory() as session, session.begin():
        dst_owner, _, _ = _approved_plan(session, clocks=(time(7),))
        assert schedule_due_reminders(session, datetime(2026, 3, 8, 11, 30, tzinfo=UTC)) == 1
        dst_reminder = session.scalar(
            select(TelegramDoseReminder).where(TelegramDoseReminder.owner_id == dst_owner.id)
        )
        assert dst_reminder is not None
        assert dst_reminder.scheduled_at == datetime(2026, 3, 8, 11, tzinfo=UTC)


def test_plan_boundary_excludes_a_slot_before_effective_start(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session, session.begin():
        boundary_owner, _, _ = _approved_plan(
            session,
            clocks=(time(7),),
            effective_from=datetime(2026, 8, 12, 8),  # noqa: DTZ001
        )
        assert schedule_due_reminders(session, datetime(2026, 8, 12, 11, 30, tzinfo=UTC)) == 0
        assert (
            session.scalar(
                select(func.count())
                .select_from(TelegramDoseReminder)
                .where(TelegramDoseReminder.owner_id == boundary_owner.id)
            )
            == 0
        )


def test_snooze_dismiss_and_record_actions_are_durable(
    factory: sessionmaker[Session],
) -> None:
    with factory() as session, session.begin():
        owner, _, _ = _approved_plan(session)
        now = datetime(2026, 8, 12, 11, 30, tzinfo=UTC)
        schedule_due_reminders(session, now)
        reminder = session.scalar(
            select(TelegramDoseReminder).where(TelegramDoseReminder.owner_id == owner.id)
        )
        assert reminder is not None
        text_reply, _ = handle_action(session, owner, reminder.id, "snooze", now=now)
        assert "30 minutes" in text_reply
        assert reminder.state is DoseReminderState.SNOOZED
        assert reminder.due_at == now + timedelta(minutes=30)
        handle_action(session, owner, reminder.id, "dismiss", now=now)
        assert reminder.state is DoseReminderState.DISMISSED

        second_owner, _, _ = _approved_plan(session)
        schedule_due_reminders(session, now)
        second = session.scalar(
            select(TelegramDoseReminder).where(TelegramDoseReminder.owner_id == second_owner.id)
        )
        assert second is not None
        record_reply, markup = handle_action(session, second_owner, second.id, "record", now=now)
        assert "Nothing is recorded until you confirm" in record_reply
        assert markup is not None
        assert second.state is DoseReminderState.RECORD_PENDING
        assert second.draft_id is not None
        draft = session.get(ExtractionDraft, second.draft_id)
        assert draft is not None and draft.state is DraftState.PENDING
        confirmed = confirm_draft(session, second_owner, draft.id)
        assert confirmed.text.startswith("Recorded:")
        assert second.state is DoseReminderState.SATISFIED

        third_owner, _, _ = _approved_plan(session)
        schedule_due_reminders(session, now)
        third = session.scalar(
            select(TelegramDoseReminder).where(TelegramDoseReminder.owner_id == third_owner.id)
        )
        assert third is not None
        handle_action(session, third_owner, third.id, "record", now=now)
        assert third.draft_id is not None
        cancelled = cancel_draft(session, third_owner, third.draft_id)
        assert cancelled.text == "Cancelled. Nothing was recorded."
        assert third.state is DoseReminderState.DISMISSED
