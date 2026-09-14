"""Curated analytics views and view-only analyst roles (ADR-0036).

Runs against real PostgreSQL provisioned by ``deploy/postgres-init`` plus migrations, so
the privilege boundary under test is the one production uses. All data is synthetic.
"""

# ruff: noqa: DTZ001 -- resolve_event_time deliberately takes naive local wall-clock times.

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.config import get_settings
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import DiaryEvent, SymptomEvent
from healthcurve.events.timekeeping import resolve_event_time
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.models import (
    GarminMetricEvent,
    GarminMetricType,
    GarminSleepEvent,
    GarminSleepKind,
    GarminSyncRun,
    GarminSyncStatus,
)
from healthcurve.medications import service as medications
from healthcurve.medications.models import (
    DoseCategory,
    DoseEvent,
    DoseTimingMode,
    DoseUnit,
    Medication,
    RegimenDoseSlot,
    Route,
)

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DIR = REPO_ROOT / "deploy" / "postgres-init"
ZONE = "America/New_York"

OWNER_PASSWORD = "owner-test-password"  # pragma: allowlist secret - ephemeral container
AI_PASSWORD = "ai-test-password"  # pragma: allowlist secret - ephemeral container
BACKUP_PASSWORD = "backup-test-password"  # pragma: allowlist secret - ephemeral container
ANALYST_PASSWORD = "analyst-test-password"  # pragma: allowlist secret - ephemeral container
ANALYST_TEXT_PASSWORD = "analyst-text-test-password"  # pragma: allowlist secret - ephemeral

PRIOR_HEAD = "9c2e7a4d1b60"


def _alembic_config() -> Config:
    config = Config(str(REPO_ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    return config


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    container = (
        PostgresContainer(
            "postgres:16-alpine",
            username="healthcurve",
            password=OWNER_PASSWORD,
            dbname="healthcurve",
            driver="psycopg",
        )
        .with_env("POSTGRES_AI_PASSWORD", AI_PASSWORD)
        .with_env("POSTGRES_BACKUP_PASSWORD", BACKUP_PASSWORD)
        .with_env("POSTGRES_ANALYST_PASSWORD", ANALYST_PASSWORD)
        .with_env("POSTGRES_ANALYST_TEXT_PASSWORD", ANALYST_TEXT_PASSWORD)
        .with_volume_mapping(str(INIT_DIR), "/docker-entrypoint-initdb.d", "ro")
    )
    with container as running:
        with mock.patch.dict(os.environ, {"HC_DATABASE_URL": running.get_connection_url()}):
            get_settings.cache_clear()
            command.upgrade(_alembic_config(), "head")
        get_settings.cache_clear()
        yield running


def _role_engine(postgres: PostgresContainer, role: str, password: str) -> Engine:
    return create_engine(
        postgres.get_connection_url().replace(
            f"healthcurve:{OWNER_PASSWORD}@", f"{role}:{password}@"
        )
    )


@pytest.fixture(scope="module")
def owner_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    engine = create_engine(postgres.get_connection_url())
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def analyst_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    engine = _role_engine(postgres, "healthcurve_analyst", ANALYST_PASSWORD)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def analyst_text_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    engine = _role_engine(postgres, "healthcurve_analyst_text", ANALYST_TEXT_PASSWORD)
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def backup_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    engine = _role_engine(postgres, "healthcurve_backup", BACKUP_PASSWORD)
    yield engine
    engine.dispose()


def _local(value: datetime) -> Any:
    return resolve_event_time(value, ZONE)


def _sleep(
    session: Session,
    owner_id: uuid.UUID,
    sync_id: uuid.UUID,
    start_local: datetime,
    end_local: datetime,
    *,
    kind: GarminSleepKind = GarminSleepKind.OVERNIGHT,
    supersedes_id: uuid.UUID | None = None,
) -> GarminSleepEvent:
    start = _local(start_local)
    ended_at = _local(end_local).occurred_at
    return events.create_event(
        session,
        GarminSleepEvent,
        owner_id=owner_id,
        event_time=start,
        source_type=SourceType.PROVIDER,
        confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
        ended_at=ended_at,
        sleep_kind=kind,
        overall_sleep_score=80,
        stage_count=0,
        duration_seconds=int((ended_at - start.occurred_at).total_seconds()),
        garmin_duration_source="provider",
        awakenings=1,
        garmin_sync_run_id=sync_id,
        garmin_source_member="daily-sleep",
        garmin_manufacturer="Garmin",
        supersedes_id=supersedes_id,
        correction_reason="Synthetic provider revision" if supersedes_id else None,
    )


def _dose(
    session: Session,
    owner_id: uuid.UUID,
    medication_id: uuid.UUID,
    taken_local: datetime,
    *,
    slot: RegimenDoseSlot | None = None,
    voided: bool = False,
    supersedes_id: uuid.UUID | None = None,
    notes: str | None = None,
) -> DoseEvent:
    return events.create_event(
        session,
        DoseEvent,
        owner_id=owner_id,
        event_time=_local(taken_local),
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        medication_id=medication_id,
        amount=Decimal("10"),
        unit=DoseUnit.MG,
        route=Route.ORAL,
        category=DoseCategory.SCHEDULED,
        regimen_version_id=None if slot is None else slot.regimen_version_id,
        slot_id=None if slot is None else slot.id,
        voided=voided,
        supersedes_id=supersedes_id,
        correction_reason="Synthetic correction" if supersedes_id else None,
        notes=notes,
    )


@pytest.fixture(scope="module", autouse=True)
def seeded(owner_engine: Engine) -> None:
    with Session(owner_engine) as session, session.begin():
        owner = Owner(
            email=f"analytics-views-{uuid.uuid4()}@example.test",
            password_hash="synthetic-not-a-login-hash",
            default_timezone=ZONE,
        )
        session.add(owner)
        session.flush()
        sync = GarminSyncRun(
            owner_id=owner.id,
            requested_start_date=date(2026, 3, 7),
            requested_end_date=date(2026, 3, 9),
            timezone=ZONE,
            status=GarminSyncStatus.COMPLETED,
            started_at=datetime(2026, 3, 9, 17, tzinfo=UTC),
            finished_at=datetime(2026, 3, 9, 17, 1, tzinfo=UTC),
            counts={},
            warning_codes=[],
            client_version="synthetic",
        )
        session.add(sync)
        session.flush()

        # Spans the 2026-03-08 spring-forward transition: 7h45m of wall clock, 6h45m elapsed.
        _sleep(
            session, owner.id, sync.id, datetime(2026, 3, 7, 23, 30), datetime(2026, 3, 8, 7, 15)
        )
        _sleep(
            session, owner.id, sync.id, datetime(2026, 3, 8, 14, 0), datetime(2026, 3, 8, 14, 20)
        )
        _sleep(
            session,
            owner.id,
            sync.id,
            datetime(2026, 3, 8, 15, 0),
            datetime(2026, 3, 8, 15, 40),
            kind=GarminSleepKind.NAP,
        )
        original_night = _sleep(
            session,
            owner.id,
            sync.id,
            datetime(2026, 3, 9, 0, 30),
            datetime(2026, 3, 9, 6, 30),
        )
        _sleep(
            session,
            owner.id,
            sync.id,
            datetime(2026, 3, 9, 0, 30),
            datetime(2026, 3, 9, 6, 45),
            supersedes_id=original_night.id,
        )

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
            version_label="Synthetic plan",
            effective_from=datetime(2026, 3, 1),
            effective_timezone=ZONE,
        )
        morning_slot = RegimenDoseSlot(
            regimen_version_id=version.id,
            medication_id=medication.id,
            timing_mode=DoseTimingMode.FIXED_TIME,
            scheduled_local_time=time(8),
            amount=Decimal("10"),
            unit=DoseUnit.MG,
            route=Route.ORAL,
        )
        late_slot = RegimenDoseSlot(
            regimen_version_id=version.id,
            medication_id=medication.id,
            timing_mode=DoseTimingMode.FIXED_TIME,
            scheduled_local_time=time(23, 50),
            amount=Decimal("5"),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            condition="Synthetic physician condition",
        )
        session.add_all([morning_slot, late_slot])
        session.flush()

        _dose(session, owner.id, medication.id, datetime(2026, 3, 8, 7, 45), slot=morning_slot)
        _dose(
            session,
            owner.id,
            medication.id,
            datetime(2026, 3, 9, 0, 10),
            slot=late_slot,
            notes="Synthetic dose note",
        )
        _dose(session, owner.id, medication.id, datetime(2026, 3, 8, 9, 0), voided=True)
        original_dose = _dose(session, owner.id, medication.id, datetime(2026, 3, 8, 12, 0))
        _dose(
            session,
            owner.id,
            medication.id,
            datetime(2026, 3, 8, 12, 5),
            supersedes_id=original_dose.id,
        )

        events.create_event(
            session,
            SymptomEvent,
            owner_id=owner.id,
            event_time=_local(datetime(2026, 3, 8, 10, 0)),
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            name="synthetic fatigue",
            severity=4,
            notes="Synthetic symptom note",
        )
        events.create_event(
            session,
            DiaryEvent,
            owner_id=owner.id,
            event_time=_local(datetime(2026, 3, 8, 21, 0)),
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            text="Synthetic diary text",
            is_sensitive=True,
        )
        for event_time, metric, value, unit, aggregation, field in (
            (
                datetime(2026, 3, 8),
                GarminMetricType.STEPS,
                "8000",
                "steps",
                "daily_summary",
                "totalSteps",
            ),
            (
                datetime(2026, 3, 8),
                GarminMetricType.RESTING_HEART_RATE,
                "55",
                "bpm",
                "daily_summary",
                "restingHeartRate",
            ),
            (
                datetime(2026, 3, 8, 8, 0),
                GarminMetricType.HEART_RATE,
                "70",
                "bpm",
                "provider_sample",
                "heartrate",
            ),
        ):
            events.create_event(
                session,
                GarminMetricEvent,
                owner_id=owner.id,
                event_time=_local(event_time),
                source_type=SourceType.PROVIDER,
                confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
                metric_type=metric,
                value=Decimal(value),
                unit=unit,
                aggregation=aggregation,
                sample_interval_seconds=120 if aggregation == "provider_sample" else None,
                garmin_sync_run_id=sync.id,
                garmin_source_member="synthetic",
                garmin_manufacturer="Garmin",
                garmin_field_name=field,
            )


@pytest.mark.safety("SAFE-15")
def test_analyst_roles_have_read_only_defaults_and_no_elevated_attributes(
    owner_engine: Engine, analyst_engine: Engine
) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT rolname, rolsuper, rolcreaterole, rolcreatedb, rolcanlogin "
                "FROM pg_roles WHERE rolname LIKE 'healthcurve_analy%' ORDER BY rolname"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        ("healthcurve_analyst", False, False, False, True),
        ("healthcurve_analyst_text", False, False, False, True),
        ("healthcurve_analytics_owner", False, False, False, False),
    ]
    with analyst_engine.connect() as conn:
        assert conn.scalar(text("SHOW default_transaction_read_only")) == "on"
        assert conn.scalar(text("SHOW statement_timeout")) == "15s"


@pytest.mark.safety("SAFE-15")
@pytest.mark.parametrize(
    "relation",
    [
        "fact.dose_event",
        "fact.diary_event",
        "plan.medication",
        "ops.wearable_daily_summary",
        "identity.owner",
        "ai.chat_message",
        "analytics_text.record_notes",
        "analytics_text.diary_entries",
    ],
)
def test_analyst_cannot_read_base_tables_identity_ai_or_text(
    analyst_engine: Engine, relation: str
) -> None:
    with pytest.raises(DBAPIError, match="permission denied"):
        with analyst_engine.connect() as conn:
            conn.execute(text(f"SELECT count(*) FROM {relation}"))


@pytest.mark.safety("SAFE-15")
@pytest.mark.parametrize(
    "statement",
    [
        "DELETE FROM analytics.symptoms",
        "CREATE TABLE analytics.model_created (id int)",
        "CREATE TEMP TABLE model_scratch (id int)",
        "INSERT INTO fact.symptom_event (id) VALUES (gen_random_uuid())",
    ],
)
def test_analyst_cannot_write_even_when_forcing_read_write(
    analyst_engine: Engine, statement: str
) -> None:
    with pytest.raises(DBAPIError, match=r"read-only transaction|permission denied|cannot"):
        with analyst_engine.begin() as conn:
            conn.execute(text(statement))
    # The role default is only one layer: privileges still deny writes in read-write mode.
    if statement.startswith("CREATE TEMP"):
        return
    with pytest.raises(DBAPIError, match=r"permission denied|cannot"):
        with analyst_engine.begin() as conn:
            conn.execute(text("SET TRANSACTION READ WRITE"))
            conn.execute(text(statement))


def test_text_is_readable_only_through_the_opt_in_role(
    analyst_engine: Engine, analyst_text_engine: Engine
) -> None:
    with analyst_text_engine.connect() as conn:
        notes = conn.execute(
            text("SELECT record_type, field, text FROM analytics_text.record_notes ORDER BY text")
        ).all()
        diary = conn.execute(
            text("SELECT text, is_sensitive FROM analytics_text.diary_entries")
        ).all()
        conditions = conn.scalars(
            text("SELECT condition FROM analytics_text.plan_slot_conditions")
        ).all()
        assert conn.scalar(text("SELECT count(*) FROM analytics.doses")) == 3
    assert [tuple(row) for row in notes] == [
        ("dose", "notes", "Synthetic dose note"),
        ("symptom", "notes", "Synthetic symptom note"),
    ]
    assert [tuple(row) for row in diary] == [("Synthetic diary text", True)]
    assert conditions == ["Synthetic physician condition"]
    with analyst_engine.connect() as conn:
        columns = set(
            conn.scalars(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema = 'analytics'"
                )
            )
        )
    assert not columns & {"notes", "text", "description", "title", "condition", "trigger"}


def test_sleep_views_select_current_overnight_sessions_across_dst(analyst_engine: Engine) -> None:
    with analyst_engine.connect() as conn:
        sessions = conn.execute(
            text(
                "SELECT sleep_kind, in_bed_minutes FROM analytics.sleep_sessions ORDER BY start_at"
            )
        ).all()
        nights = conn.execute(
            text(
                "SELECT wake_date, bedtime_clock, wake_clock, bedtime_minutes_after_noon, "
                "wake_minutes_after_midnight, in_bed_minutes, overnight_sessions_ending_that_date "
                "FROM analytics.sleep_nights ORDER BY wake_date"
            )
        ).all()
    assert [tuple(row) for row in sessions] == [
        ("overnight", Decimal("405.0")),
        ("overnight", Decimal("20.0")),
        ("nap", Decimal("40.0")),
        ("overnight", Decimal("375.0")),
    ]
    assert [tuple(row) for row in nights] == [
        (
            date(2026, 3, 8),
            time(23, 30),
            time(7, 15),
            Decimal("690.0"),
            Decimal("435.0"),
            Decimal("405.0"),
            2,
        ),
        (
            date(2026, 3, 9),
            time(0, 30),
            time(6, 45),
            Decimal("750.0"),
            Decimal("405.0"),
            Decimal("375.0"),
            1,
        ),
    ]


def test_dose_view_excludes_voided_and_superseded_and_derives_timing(
    analyst_engine: Engine,
) -> None:
    with analyst_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT local_date, local_clock, medication_name, slot_scheduled_clock, "
                "minutes_from_scheduled, minutes_after_wake FROM analytics.doses ORDER BY taken_at"
            )
        ).all()
    assert [tuple(row) for row in rows] == [
        (date(2026, 3, 8), time(7, 45), "Synthetic hydrocortisone", time(8), -15, 30),
        (date(2026, 3, 8), time(12, 5), "Synthetic hydrocortisone", None, None, 290),
        # Scheduled 23:50 the previous evening; the wrap keeps this 20 minutes late.
        (date(2026, 3, 9), time(0, 10), "Synthetic hydrocortisone", time(23, 50), 20, -395),
    ]


def test_wearable_views_keep_daily_values_and_samples_distinct(analyst_engine: Engine) -> None:
    with analyst_engine.connect() as conn:
        daily = conn.execute(
            text(
                "SELECT local_date, steps, resting_heart_rate_bpm, average_stress_score "
                "FROM analytics.daily_wearables"
            )
        ).all()
        samples = conn.execute(
            text(
                "SELECT metric_type, value, sample_interval_seconds FROM analytics.wearable_samples"
            )
        ).all()
        symptoms = conn.execute(text("SELECT name, severity FROM analytics.symptoms")).all()
    assert [tuple(row) for row in daily] == [
        (date(2026, 3, 8), Decimal("8000.0000"), Decimal("55.0000"), None)
    ]
    assert [tuple(row) for row in samples] == [("heart_rate", Decimal("70.0000"), 120)]
    assert [tuple(row) for row in symptoms] == [("synthetic fatigue", 4)]


def test_analyst_init_script_is_idempotent_on_an_existing_database(
    postgres: PostgresContainer, analyst_engine: Engine
) -> None:
    result = postgres.exec(["sh", "/docker-entrypoint-initdb.d/03-analyst-roles.sh"])
    assert result.exit_code == 0, result.output
    analyst_engine.dispose()
    with analyst_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM analytics.sleep_nights")) == 2


def test_backup_role_can_dump_analytics_views(backup_engine: Engine) -> None:
    with backup_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM analytics.doses")) == 3
        assert conn.scalar(text("SELECT count(*) FROM analytics_text.record_notes")) == 2


def test_migration_downgrades_and_reapplies_cleanly(
    postgres: PostgresContainer, owner_engine: Engine, analyst_engine: Engine
) -> None:
    with mock.patch.dict(os.environ, {"HC_DATABASE_URL": postgres.get_connection_url()}):
        get_settings.cache_clear()
        command.downgrade(_alembic_config(), PRIOR_HEAD)
        with owner_engine.connect() as conn:
            assert (
                conn.scalar(
                    text(
                        "SELECT count(*) FROM pg_namespace "
                        "WHERE nspname IN ('analytics', 'analytics_text')"
                    )
                )
                == 0
            )
        command.upgrade(_alembic_config(), "head")
    get_settings.cache_clear()
    analyst_engine.dispose()
    with analyst_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM analytics.doses")) == 3
