"""The zone ledger against real PostgreSQL: constraints, and a reversible migration.

The unit tests use SQLite, which enforces neither the foreign key nor the unique
constraint the ledger's correctness rests on, and stores a timestamp without its
offset. Those three things are checked here, where they are real.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.config import get_settings
from healthcurve.identity import timezones
from healthcurve.identity.models import Owner, TimezoneStay, TimezoneStaySource

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DIR = REPO_ROOT / "deploy" / "postgres-init"
OWNER_PASSWORD = "owner-test-password"  # pragma: allowlist secret - ephemeral container
AI_PASSWORD = "ai-test-password"  # pragma: allowlist secret - ephemeral container

#: The revision immediately before the ledger. Downgrading to it must leave no trace.
PRIOR_HEAD = "b6d2e8f4a371"  # pragma: allowlist secret - Alembic revision ID

HOME = "America/New_York"
AWAY = "America/Chicago"


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return config


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    container = PostgresContainer(
        "postgres:16-alpine",
        username="healthcurve",
        password=OWNER_PASSWORD,
        dbname="healthcurve",
        driver="psycopg",
    )
    # The init scripts refuse to provision the restricted AI role without this, and
    # a failed init script stops the container before it ever accepts a connection.
    container = container.with_env("POSTGRES_AI_PASSWORD", AI_PASSWORD).with_volume_mapping(
        str(INIT_DIR), "/docker-entrypoint-initdb.d", "ro"
    )
    with container as running:
        with mock.patch.dict(os.environ, {"HC_DATABASE_URL": running.get_connection_url()}):
            get_settings.cache_clear()
            command.upgrade(_alembic_config(), "head")
        get_settings.cache_clear()
        yield running


@pytest.fixture(scope="module")
def engine(postgres: PostgresContainer) -> Iterator[Engine]:
    created = create_engine(postgres.get_connection_url())
    yield created
    created.dispose()


@pytest.fixture
def owner(engine: Engine) -> Iterator[Owner]:
    with Session(engine) as session:
        row = Owner(
            id=uuid.uuid4(),
            email=f"traveller-{uuid.uuid4().hex[:8]}@example.test",
            password_hash="synthetic-not-a-real-hash",  # pragma: allowlist secret
            default_timezone=HOME,
        )
        session.add(row)
        session.commit()
        yield row
        session.delete(row)
        session.commit()


def test_a_stay_survives_the_round_trip_with_its_offset(engine: Engine, owner: Owner) -> None:
    """SQLite loses the offset here; ``timestamptz`` is why the resolver can trust it."""
    started = datetime(2026, 9, 20, 14, 30, tzinfo=UTC)
    with Session(engine) as session:
        session.add(session.merge(owner))
        timezones.record_stay(
            session, owner, AWAY, source=TimezoneStaySource.TELEGRAM, started_at=started
        )
        session.commit()

    with Session(engine) as session:
        stay = timezones.history(session, owner)[0]
        assert stay.started_at.tzinfo is not None
        assert stay.started_at == started
        assert stay.timezone == AWAY
        assert stay.source is TimezoneStaySource.TELEGRAM


def test_two_stays_cannot_begin_at_the_same_instant(engine: Engine, owner: Owner) -> None:
    """The constraint behind "no representable gap or overlap"."""
    started = datetime(2026, 9, 20, 14, 30, tzinfo=UTC)
    with Session(engine) as session, pytest.raises(IntegrityError):
        session.add_all(
            TimezoneStay(
                id=uuid.uuid4(),
                owner_id=owner.id,
                timezone=zone,
                started_at=started,
                source=TimezoneStaySource.TELEGRAM,
            )
            for zone in (AWAY, "Asia/Tokyo")
        )
        session.commit()


def test_deleting_the_owner_takes_the_ledger_with_it(engine: Engine) -> None:
    """A stay is location data; it must not outlive the account it describes."""
    owner_id = uuid.uuid4()
    with Session(engine) as session:
        session.add(
            Owner(
                id=owner_id,
                email=f"erased-{uuid.uuid4().hex[:8]}@example.test",
                password_hash="synthetic-not-a-real-hash",  # pragma: allowlist secret
                default_timezone=HOME,
            )
        )
        session.flush()
        session.add(
            TimezoneStay(
                id=uuid.uuid4(),
                owner_id=owner_id,
                timezone=AWAY,
                started_at=datetime.now(UTC),
                source=TimezoneStaySource.WEB,
            )
        )
        session.commit()

    with Session(engine) as session:
        session.delete(session.get(Owner, owner_id))
        session.commit()

    with engine.connect() as conn:
        remaining = conn.execute(
            text("SELECT count(*) FROM identity.timezone_stay WHERE owner_id = :owner"),
            {"owner": owner_id},
        ).scalar_one()
    assert remaining == 0


def test_resolution_across_a_boundary_matches_the_ledger(engine: Engine, owner: Owner) -> None:
    departure = datetime(2026, 9, 20, 14, 0, tzinfo=UTC)
    with Session(engine) as session:
        timezones.record_stay(
            session, owner, AWAY, source=TimezoneStaySource.TELEGRAM, started_at=departure
        )
        session.commit()

    with Session(engine) as session:
        assert timezones.zone_at(session, owner, departure - timedelta(seconds=1)) == HOME
        assert timezones.zone_at(session, owner, departure) == AWAY
        assert timezones.current_zone(session, owner, now=departure + timedelta(days=1)) == AWAY


def test_the_migration_downgrades_and_reapplies_cleanly(postgres: PostgresContainer) -> None:
    def table_count(engine: Engine) -> int:
        with engine.connect() as conn:
            return conn.execute(
                text(
                    "SELECT count(*) FROM information_schema.tables "
                    "WHERE table_schema = 'identity' AND table_name = 'timezone_stay'"
                )
            ).scalar_one()

    probe = create_engine(postgres.get_connection_url())
    try:
        with mock.patch.dict(os.environ, {"HC_DATABASE_URL": postgres.get_connection_url()}):
            get_settings.cache_clear()
            assert table_count(probe) == 1

            command.downgrade(_alembic_config(), PRIOR_HEAD)
            assert table_count(probe) == 0

            command.upgrade(_alembic_config(), "head")
            assert table_count(probe) == 1
        get_settings.cache_clear()
    finally:
        probe.dispose()
