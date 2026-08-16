"""PostgreSQL proofs for chatbot owner isolation and transaction read-only mode."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
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
from healthcurve.identity.models import Owner
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
