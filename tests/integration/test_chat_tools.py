"""PostgreSQL proofs for chatbot owner isolation and transaction read-only mode."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.chat.tools import execute_chat_tool
from healthcurve.db import SCHEMAS, Base
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import SymptomEvent
from healthcurve.events.timekeeping import from_instant
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.models import (
    GarminSleepEvent,
    GarminSyncOrigin,
    GarminSyncRun,
    GarminSyncStatus,
)
from healthcurve.vitals.models import MeasurementSetting, WeightEvent, WeightUnit
from tests.fixtures.synthetic import SYNTHETIC_MARKER

pytestmark = [pytest.mark.postgres, pytest.mark.slow]


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        engine = create_engine(container.get_connection_url())
        with engine.begin() as connection:
            for schema in SCHEMAS:
                connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        Base.metadata.create_all(engine)
        yield engine
        engine.dispose()


def owner(email: str) -> Owner:
    return Owner(
        id=uuid.uuid4(),
        email=email,
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="America/New_York",
    )


def weight(owner_id: uuid.UUID, value: str) -> WeightEvent:
    occurred = datetime(2026, 8, 10, 12, tzinfo=UTC)
    return WeightEvent(
        owner_id=owner_id,
        occurred_at=occurred,
        local_time=datetime(2026, 8, 10, 8),  # noqa: DTZ001 - wall time is naive by design
        timezone="America/New_York",
        utc_offset_minutes=-240,
        recorded_at=occurred,
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        value=Decimal(value),
        unit=WeightUnit.LB,
        normalized_kg=Decimal(value) * Decimal("0.45359237"),
        measurement_setting=MeasurementSetting.HOME,
    )


def symptom(owner_id: uuid.UUID, *, name: str, hour: int) -> SymptomEvent:
    occurred = datetime(2026, 8, 10, hour, tzinfo=UTC)
    return SymptomEvent(
        owner_id=owner_id,
        occurred_at=occurred,
        local_time=datetime(2026, 8, 10, hour - 4),  # noqa: DTZ001 - wall time is naive
        timezone="America/New_York",
        utc_offset_minutes=-240,
        recorded_at=occurred,
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        name=name,
    )


def sleep(
    owner_id: uuid.UUID,
    *,
    sync_run_id: uuid.UUID,
    provider_id: str,
    ended_at: datetime,
) -> GarminSleepEvent:
    started_at = ended_at.replace(hour=3)
    event = GarminSleepEvent(
        owner_id=owner_id,
        recorded_at=datetime(2026, 8, 12, 13, tzinfo=UTC),
        source_type=SourceType.PROVIDER,
        provider_id=provider_id,
        source_revision="synthetic-v1",
        confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
        garmin_sync_run_id=sync_run_id,
        garmin_source_member=provider_id,
        garmin_manufacturer="Garmin",
        garmin_product_name="Synthetic Test Device",
        ended_at=ended_at,
        overall_sleep_score=80,
        stage_count=0,
        duration_seconds=int((ended_at - started_at).total_seconds()),
        garmin_duration_source="provider",
        awakenings=0,
    )
    event.apply_event_time(from_instant(started_at, "America/New_York"))
    return event


def test_timeline_tool_never_returns_another_owners_fact(engine: Engine) -> None:
    first = owner("chat-first@example.test")
    second = owner("chat-second@example.test")
    with Session(engine) as session, session.begin():
        session.add_all([first, second])
        session.flush()
        session.add_all([weight(first.id, "175"), weight(second.id, "250")])
        first_id = first.id

    with Session(engine) as session:
        result = execute_chat_tool(
            session,
            owner_id=first_id,
            tool_name="search_timeline",
            arguments={
                "date_from": "2026-08-10",
                "date_to": "2026-08-10",
                "timezone": "America/New_York",
                "record_types": ["weight"],
            },
        )

    items = result.data["items"]
    assert isinstance(items, list)
    assert len(items) == 1
    assert items[0]["value"] == "175.0000"
    assert result.source_manifest["fact"] == [items[0]["id"]]


def test_recent_aggregate_summaries_are_owner_scoped(engine: Engine) -> None:
    first = owner("chat-aggregate-first@example.test")
    second = owner("chat-aggregate-second@example.test")
    with Session(engine) as session, session.begin():
        session.add_all([first, second])
        session.flush()
        sync_runs = [
            GarminSyncRun(
                owner_id=account.id,
                requested_start_date=date(2026, 8, 10),
                requested_end_date=date(2026, 8, 11),
                timezone="America/New_York",
                origin=GarminSyncOrigin.MANUAL,
                status=GarminSyncStatus.COMPLETED,
                started_at=datetime(2026, 8, 12, 12, tzinfo=UTC),
                finished_at=datetime(2026, 8, 12, 12, 1, tzinfo=UTC),
                counts={"sleep": 2},
                warning_codes=[],
                client_version="synthetic-test",
            )
            for account in (first, second)
        ]
        session.add_all(sync_runs)
        session.flush()
        session.add_all(
            [
                symptom(first.id, name="synthetic fatigue", hour=14),
                symptom(first.id, name="synthetic dizziness", hour=15),
                symptom(second.id, name="other owner symptom", hour=16),
                sleep(
                    first.id,
                    sync_run_id=sync_runs[0].id,
                    provider_id="synthetic-first-0600",
                    ended_at=datetime(2026, 8, 10, 10, tzinfo=UTC),
                ),
                sleep(
                    first.id,
                    sync_run_id=sync_runs[0].id,
                    provider_id="synthetic-first-0800",
                    ended_at=datetime(2026, 8, 11, 12, tzinfo=UTC),
                ),
                sleep(
                    second.id,
                    sync_run_id=sync_runs[1].id,
                    provider_id="synthetic-second-1100",
                    ended_at=datetime(2026, 8, 10, 15, tzinfo=UTC),
                ),
            ]
        )
        first_id = first.id

    common: dict[str, object] = {
        "date_from": "2026-08-10",
        "date_to": "2026-08-11",
        "timezone": "America/New_York",
    }
    with Session(engine) as session:
        symptom_result = execute_chat_tool(
            session,
            owner_id=first_id,
            tool_name="get_symptom_episode_context",
            arguments=common,
        )
    with Session(engine) as session:
        sleep_result = execute_chat_tool(
            session,
            owner_id=first_id,
            tool_name="search_timeline",
            arguments={**common, "record_types": ["garmin_sleep"]},
        )

    assert symptom_result.data["symptom_count"] == 2
    assert sleep_result.data["record_counts"] == {"garmin_sleep": 2}
    assert sleep_result.data["wake_time_summary"] == {
        "sample_count": 2,
        "average_local_time": "07:00",
        "average_local_hour": 7,
        "average_local_minute": 0,
    }


def test_tool_transaction_is_enforced_read_only_by_postgresql(engine: Engine) -> None:
    account = owner("chat-read-only@example.test")
    with Session(engine) as session, session.begin():
        session.add(account)
        session.flush()
        owner_id = account.id

    with Session(engine) as session:
        execute_chat_tool(
            session,
            owner_id=owner_id,
            tool_name="get_data_availability",
            arguments={
                "date_from": "2026-08-10",
                "date_to": "2026-08-10",
                "timezone": "America/New_York",
            },
        )
        session.add(owner("must-not-write@example.test"))
        with pytest.raises(DBAPIError, match="read-only transaction"):
            session.flush()
