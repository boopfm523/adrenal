"""SAFE-15 / SAFE-16 enforced as a database privilege, not a convention.

These tests run against a real PostgreSQL started from ``deploy/postgres-init``, so
they verify the file that actually provisions production -- not a re-statement of it.
SQLite cannot express any of this, which is why ADR-0001 requires real PostgreSQL here.

They also cover the failure this build actually hit: the init script silently failing
and PostgreSQL starting anyway with no schemas. A missing schema or a missing revoke
now fails the suite instead of quietly widening what AI can write.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from pathlib import Path
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import ProgrammingError
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from healthcurve.chat.tools import execute_chat_tool
from healthcurve.config import get_settings
from healthcurve.identity.models import Owner

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DIR = REPO_ROOT / "deploy" / "postgres-init"

OWNER_PASSWORD = "owner-test-password"  # ephemeral container credential
AI_PASSWORD = "ai-test-password"  # ephemeral container credential
BACKUP_PASSWORD = "backup-test-password"  # ephemeral read-only dump credential

SAFETY_SCHEMAS = ("fact", "plan")


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    container = (
        PostgresContainer(
            "postgres:16-alpine",
            username="healthcurve",
            password=OWNER_PASSWORD,
            dbname="healthcurve",
            driver="psycopg",  # psycopg 3, matching the pinned application driver
        )
        .with_env("POSTGRES_AI_PASSWORD", AI_PASSWORD)
        .with_env("POSTGRES_BACKUP_PASSWORD", BACKUP_PASSWORD)
        .with_volume_mapping(str(INIT_DIR), "/docker-entrypoint-initdb.d", "ro")
    )
    with container as running:
        alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
        alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        with mock.patch.dict(os.environ, {"HC_DATABASE_URL": running.get_connection_url()}):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()
        yield running


@pytest.fixture(scope="module")
def owner_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    engine = create_engine(postgres.get_connection_url())
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def ai_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    url = postgres.get_connection_url().replace(
        f"healthcurve:{OWNER_PASSWORD}@", f"healthcurve_ai:{AI_PASSWORD}@"
    )
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def backup_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    url = postgres.get_connection_url().replace(
        f"healthcurve:{OWNER_PASSWORD}@", f"healthcurve_backup:{BACKUP_PASSWORD}@"
    )
    engine = create_engine(url)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module", autouse=True)
def probe_tables(owner_engine: Engine) -> None:
    """Owner-created tables in each safety schema for the AI role to be denied on."""
    with owner_engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS fact.probe (id int primary key, note text)"))
        conn.execute(text("CREATE TABLE IF NOT EXISTS plan.probe (id int primary key, note text)"))
        conn.execute(text("INSERT INTO fact.probe VALUES (1, 'owner') ON CONFLICT DO NOTHING"))
        conn.execute(text("INSERT INTO plan.probe VALUES (1, 'owner') ON CONFLICT DO NOTHING"))


# ---------------------------------------------------------------------------
# The init script ran at all
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-01")
def test_all_four_schemas_exist(owner_engine: Engine) -> None:
    """Guards the silent-init-failure mode: PostgreSQL starting with no schemas."""
    with owner_engine.connect() as conn:
        found = {
            row[0]
            for row in conn.execute(
                text("SELECT nspname FROM pg_namespace WHERE nspname IN ('fact','plan','ai','ops')")
            )
        }
    assert found == {"fact", "plan", "ai", "ops"}, (
        f"schema partition incomplete: {sorted(found)}. deploy/postgres-init did not run correctly."
    )


def test_required_extensions_are_installed(owner_engine: Engine) -> None:
    """btree_gist backs the non-overlapping regimen version constraint (ADR-0001)."""
    with owner_engine.connect() as conn:
        found = {
            row[0]
            for row in conn.execute(
                text("SELECT extname FROM pg_extension WHERE extname IN ('btree_gist','pgcrypto')")
            )
        }
    assert found == {"btree_gist", "pgcrypto"}


def test_ai_role_exists(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        assert (
            conn.execute(
                text("SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai'")
            ).scalar_one_or_none()
            == 1
        )


def test_backup_role_exists_and_is_not_superuser(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        row = conn.execute(
            text(
                "SELECT rolsuper, rolcreaterole, rolcreatedb "
                "FROM pg_roles WHERE rolname = 'healthcurve_backup'"
            )
        ).one()
    assert row == (False, False, False)


def test_backup_role_can_read_but_not_write(owner_engine: Engine, backup_engine: Engine) -> None:
    with owner_engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS identity.backup_probe (id int primary key)"))
        conn.execute(text("INSERT INTO identity.backup_probe VALUES (1) ON CONFLICT DO NOTHING"))

    with backup_engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM identity.backup_probe")).scalar_one() == 1

    with pytest.raises(ProgrammingError, match="permission denied"):
        with backup_engine.begin() as conn:
            conn.execute(text("INSERT INTO identity.backup_probe VALUES (2)"))


def test_backup_role_can_manage_only_backup_queue_rows(backup_engine: Engine) -> None:
    identifier = uuid.uuid4()
    with backup_engine.begin() as conn:
        conn.execute(
            text(
                "INSERT INTO ops.job "
                "(id, task, payload, idempotency_key, status, priority, attempt_count, "
                "max_attempts, run_at) VALUES "
                "(:id, 'backup.nightly', '{}'::jsonb, 'synthetic-role-test', 'queued', "
                "100, 0, 4, now())"
            ),
            {"id": identifier},
        )
        conn.execute(
            text("UPDATE ops.job SET status = 'completed' WHERE id = :id"), {"id": identifier}
        )
        assert (
            conn.execute(
                text("SELECT status FROM ops.job WHERE id = :id"), {"id": identifier}
            ).scalar_one()
            == "completed"
        )

    with pytest.raises(ProgrammingError, match="permission denied"):
        with backup_engine.begin() as conn:
            conn.execute(text("DELETE FROM ops.job WHERE id = :id"), {"id": identifier})

    with pytest.raises(ProgrammingError, match="permission denied"):
        with backup_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ops.audit_entry (id, actor, action) "
                    "VALUES (:id, 'system', 'record_created')"
                ),
                {"id": uuid.uuid4()},
            )


# ---------------------------------------------------------------------------
# The privilege boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("schema", SAFETY_SCHEMAS)
def test_ai_role_can_read_facts_and_plans(ai_engine: Engine, schema: str) -> None:
    """AI must be able to read what it cites -- the restriction is on writing."""
    with ai_engine.connect() as conn:
        assert conn.execute(text(f"SELECT count(*) FROM {schema}.probe")).scalar_one() == 1


@pytest.mark.safety("SAFE-15")
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO fact.probe VALUES (99, 'ai-written')",
        "UPDATE fact.probe SET note = 'tampered'",
        "DELETE FROM fact.probe",
    ],
)
def test_ai_role_cannot_write_facts(ai_engine: Engine, statement: str) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        with ai_engine.begin() as conn:
            conn.execute(text(statement))


@pytest.mark.safety("SAFE-16")
@pytest.mark.parametrize(
    "statement",
    [
        "INSERT INTO plan.probe VALUES (99, 'ai-approved')",
        "UPDATE plan.probe SET note = 'approved'",
        "DELETE FROM plan.probe",
    ],
)
def test_ai_role_cannot_write_plans(ai_engine: Engine, statement: str) -> None:
    with pytest.raises(ProgrammingError, match="permission denied"):
        with ai_engine.begin() as conn:
            conn.execute(text(statement))


@pytest.mark.safety("SAFE-15")
@pytest.mark.parametrize("schema", SAFETY_SCHEMAS)
def test_tables_created_later_are_also_protected(
    owner_engine: Engine, ai_engine: Engine, schema: str
) -> None:
    """The real risk is a future migration adding a table without a revoke.

    ALTER DEFAULT PRIVILEGES is what makes a *new* table protected on creation. Without
    this test, a forgotten grant statement in a migration would silently give AI write
    access to whatever table that migration added.
    """
    table = f"{schema}.future_migration_table"
    with owner_engine.begin() as conn:
        conn.execute(text(f"CREATE TABLE IF NOT EXISTS {table} (id int primary key)"))

    with pytest.raises(ProgrammingError, match="permission denied"):
        with ai_engine.begin() as conn:
            conn.execute(text(f"INSERT INTO {table} VALUES (1)"))


def test_ai_role_owns_its_own_namespace(ai_engine: Engine) -> None:
    """The restriction must not make the AI module unable to do its own work."""
    with ai_engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS ai.analysis_probe (id int primary key)"))
        conn.execute(text("INSERT INTO ai.analysis_probe VALUES (1) ON CONFLICT DO NOTHING"))
        assert conn.execute(text("SELECT count(*) FROM ai.analysis_probe")).scalar_one() == 1


@pytest.mark.safety("SAFE-15")
def test_chat_tables_have_deliberate_ai_and_backup_privileges(owner_engine: Engine) -> None:
    """Chat working state is writable by AI; backup remains strictly read-only."""
    tables = ("chat_conversation", "chat_message", "chat_tool_execution")
    with owner_engine.connect() as conn:
        for table in tables:
            qualified = f"ai.{table}"
            assert conn.scalar(
                text("SELECT has_table_privilege(:role, :table, 'SELECT,INSERT,UPDATE,DELETE')"),
                {"role": "healthcurve_ai", "table": qualified},
            )
            assert conn.scalar(
                text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
                {"role": "healthcurve_backup", "table": qualified},
            )
            assert not conn.scalar(
                text("SELECT has_table_privilege(:role, :table, 'INSERT')"),
                {"role": "healthcurve_backup", "table": qualified},
            )


def test_ai_role_can_record_job_progress(owner_engine: Engine, ai_engine: Engine) -> None:
    """AI jobs write to ops, so the restriction must not block the queue (ADR-0004)."""
    with owner_engine.begin() as conn:
        conn.execute(text("CREATE TABLE IF NOT EXISTS ops.job_probe (id int primary key)"))
    with ai_engine.begin() as conn:
        conn.execute(text("INSERT INTO ops.job_probe VALUES (1) ON CONFLICT DO NOTHING"))


@pytest.mark.safety("SAFE-15")
def test_wearable_summary_projection_is_read_only_to_ai_and_backup(
    ai_engine: Engine, backup_engine: Engine
) -> None:
    with ai_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM ops.wearable_daily_summary")).scalar_one() == 0
        )
    with backup_engine.connect() as conn:
        assert (
            conn.execute(text("SELECT count(*) FROM ops.wearable_daily_summary")).scalar_one() == 0
        )
    with pytest.raises(ProgrammingError, match="permission denied"):
        with ai_engine.begin() as conn:
            conn.execute(text("TRUNCATE ops.wearable_daily_summary"))
    with pytest.raises(ProgrammingError, match="permission denied"):
        with backup_engine.begin() as conn:
            conn.execute(text("TRUNCATE ops.wearable_daily_summary"))


@pytest.mark.safety("SAFE-15")
def test_cortisol_pk_assumptions_are_not_writable_by_ai_or_backup(
    owner_engine: Engine,
    ai_engine: Engine,
    backup_engine: Engine,
) -> None:
    table = "ops.cortisol_pk_parameter_revision"
    with owner_engine.connect() as conn:
        assert conn.scalar(
            text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
            {"role": "healthcurve_ai", "table": table},
        )
        assert conn.scalar(
            text("SELECT has_table_privilege(:role, :table, 'SELECT')"),
            {"role": "healthcurve_backup", "table": table},
        )
        for role in ("healthcurve_ai", "healthcurve_backup"):
            for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                assert not conn.scalar(
                    text("SELECT has_table_privilege(:role, :table, :privilege)"),
                    {"role": role, "table": table, "privilege": privilege},
                )

    with pytest.raises(ProgrammingError, match="permission denied"):
        with ai_engine.begin() as conn:
            conn.execute(
                text(
                    "INSERT INTO ops.cortisol_pk_parameter_revision "
                    "(id, owner_id, revision_number, elimination_half_life_hours, "
                    "peak_time_hours, distribution_volume_liters, oral_bioavailability, "
                    "source_revision) VALUES "
                    "(:id, :owner_id, 1, 1.6, 1.1, 38.7, 0.95, 'synthetic')"
                ),
                {"id": uuid.uuid4(), "owner_id": uuid.uuid4()},
            )


@pytest.mark.safety("SAFE-15")
def test_restricted_chat_role_can_build_preceding_curve_context(
    owner_engine: Engine,
    ai_engine: Engine,
) -> None:
    account = Owner(
        email=f"chat-parameter-read-{uuid.uuid4()}@example.test",
        password_hash="synthetic-not-a-login-hash",
        default_timezone="America/New_York",
    )
    with Session(owner_engine) as session, session.begin():
        session.add(account)
        session.flush()
        owner_id = account.id

    with Session(ai_engine) as session:
        result = execute_chat_tool(
            session,
            owner_id=owner_id,
            tool_name="get_preceding_health_context",
            arguments={
                "anchor_at": "2026-08-15T13:00:00-04:00",
                "timezone": "America/New_York",
                "lookback_hours": 6,
                "history_days": 30,
                "similar_limit": 5,
                "include_stress_episode_anchors": False,
            },
        )

    assert result.tool_name == "get_preceding_health_context"
    assert isinstance(result.data["modeled_curve_at_anchor"], dict)


@pytest.mark.safety("SAFE-15")
@pytest.mark.parametrize("schema", SAFETY_SCHEMAS)
def test_ai_role_cannot_create_tables_in_safety_schemas(ai_engine: Engine, schema: str) -> None:
    """Creating a table in `fact` would let AI own -- and therefore write -- it."""
    with pytest.raises(ProgrammingError, match="permission denied"):
        with ai_engine.begin() as conn:
            conn.execute(text(f"CREATE TABLE {schema}.ai_created (id int)"))


@pytest.mark.safety("SAFE-15")
def test_ai_role_cannot_read_identity(ai_engine: Engine) -> None:
    """The AI role has no business reading credentials -- and this is deliberate.

    `identity.owner` holds the owner's email and password hash. The denial is also
    why the worker cannot simply run as this role: it must look up the owner to
    handle any message at all. That is a reason to give the worker two connections,
    not a reason to widen this one.
    """
    with pytest.raises(ProgrammingError, match="permission denied"):
        with ai_engine.begin() as conn:
            conn.execute(text("SELECT count(*) FROM identity.owner"))
