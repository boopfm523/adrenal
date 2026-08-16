"""End-to-end safety behaviour of the API.

Runs against real PostgreSQL with the real migrations, because most of what is asserted
here is only true if the database constraints exist.
"""

from __future__ import annotations

import json
import os
import uuid
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from pathlib import Path
from statistics import median
from threading import Barrier
from time import perf_counter
from typing import Any, cast
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.routing import APIRoute
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, insert, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

from healthcurve import models as all_models
from healthcurve import privacy
from healthcurve.ai import analysis as analysis_service
from healthcurve.ai.models import (
    AIAnalysis,
    AnalysisType,
    DraftState,
    ExtractionDraft,
    TelegramConversationContext,
)
from healthcurve.ai.ollama import ModelOutcome, ModelResult, OllamaClient
from healthcurve.analytics import day_analysis as day_analysis_service
from healthcurve.analytics import exposure, wake_pharmacokinetics, wake_reference_inputs
from healthcurve.analytics import service as analytics_service
from healthcurve.api import deps as api_deps
from healthcurve.api.routers import events as events_router
from healthcurve.chat import service as chat_service
from healthcurve.chat.jobs import CHAT_RESPONSE_TASK
from healthcurve.chat.models import ChatConversation, ChatMessage, ChatMessageState, ChatRole
from healthcurve.config import Environment, Settings, get_settings
from healthcurve.context.models import (
    ContextEvent,
    LocationPrecision,
)
from healthcurve.context.models import (
    TemperatureUnit as ContextTemperatureUnit,
)
from healthcurve.data_quality import findings_for_owner
from healthcurve.development_cleanup import (
    SyntheticBootstrapCleanupError,
    execute_synthetic_bootstrap_cleanup,
    preview_synthetic_bootstrap,
)
from healthcurve.document_worker import process_available, validate_one
from healthcurve.episodes.models import (
    EmergencyInjectionEvent,
    EpisodeSeverity,
    EpisodeStatus,
    StressEpisode,
)
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import DiaryEvent, SymptomEvent
from healthcurve.events.timekeeping import from_instant, resolve_event_time
from healthcurve.identity import service as auth
from healthcurve.identity.models import AuthSession, Owner
from healthcurve.identity.recovery import recover_owner_access
from healthcurve.integrations.garmin.connect_intraday import map_intraday_day
from healthcurve.integrations.garmin.connect_jobs import (
    GARMIN_DISCONNECT_TASK,
    make_disconnect_handler,
)
from healthcurve.integrations.garmin.connect_mapping import map_activities, map_day
from healthcurve.integrations.garmin.connect_sync import FetchedWindow, persist_window
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminConnection,
    GarminConnectionState,
    GarminImportBatch,
    GarminMetricEvent,
    GarminMetricType,
    GarminSleepEvent,
    GarminSleepStageInterval,
    GarminSyncOrigin,
    GarminSyncRun,
    GarminSyncStatus,
    WearableDailySummary,
)
from healthcurve.labs.cleanup_jobs import (
    LAB_DOCUMENT_CLEANUP_TASK,
    make_document_cleanup_handler,
)
from healthcurve.labs.documents import DocumentLayout
from healthcurve.labs.models import LabDocument, LabDocumentStatus, LabPanel, LabResult
from healthcurve.labs.service import backfill_normalizations
from healthcurve.medications import service as medication_service
from healthcurve.medications.models import (
    ApprovedInstruction,
    DoseCategory,
    DoseEvent,
    DoseUnit,
    InstructionCategory,
    Medication,
    RegimenDoseSlot,
    RegimenVersion,
    Route,
)
from healthcurve.operations import audit
from healthcurve.operations import worker as queue_worker
from healthcurve.operations.audit import AuditAction, AuditEntry
from healthcurve.operations.jobs import Job, JobQueueError, JobStatus
from healthcurve.operations.rate_limit import (
    RateLimiter,
    RateLimitExceeded,
    RateLimitResult,
)
from healthcurve.operations.restore_drill import assert_restore_sentinel
from healthcurve.operations.telemetry import OperationalEvent
from healthcurve.private_exports.jobs import make_cleanup_handler, make_generation_handler
from healthcurve.private_exports.models import PrivateExport
from healthcurve.private_exports.service import PRIVATE_EXPORT_TASK, request_export
from healthcurve.reports import builder as report_builder
from healthcurve.reports import service as report_service
from healthcurve.reports.cleanup_jobs import (
    REPORT_ARTIFACT_CLEANUP_TASK,
    make_snapshot_artifact_cleanup_handler,
)
from healthcurve.reports.models import ReportArtifact, ReportSnapshot
from healthcurve.vitals.models import (
    BloodPressureEvent,
    TemperatureEvent,
    TemperatureUnit,
    WeightEvent,
    WeightUnit,
)
from tests.fixtures.garmin import synthetic_activity_csv, synthetic_fit
from tests.fixtures.pdf import (
    OcrToolRunner,
    QpdfRunner,
    synthetic_scanned_lab_pdf,
    synthetic_text_lab_pdf,
)

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "correct-horse-battery-staple"
EMAIL = "owner@example.com"


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    container = PostgresContainer(
        "postgres:16-alpine",
        username="healthcurve",
        password="test-password",
        dbname="healthcurve",
        driver="psycopg",
    )
    with container as running:
        yield running


@pytest.fixture(scope="module")
def settings(postgres: PostgresContainer, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return Settings(
        # Never read the developer's .env: a real HC_OLLAMA_BASE_URL once made this
        # fixture fail startup validation for reasons unrelated to the test.
        _env_file=None,  # type: ignore[call-arg]
        database_url=postgres.get_connection_url(),
        ollama_base_url="http://ollama:11434",
        uploads_dir=tmp_path_factory.mktemp("api-uploads"),
        report_artifacts_dir=tmp_path_factory.mktemp("api-reports"),
    )


@pytest.fixture(scope="module")
def engine(settings: Settings, postgres: PostgresContainer) -> Iterator[Engine]:
    eng = create_engine(settings.database_url)
    with eng.begin() as conn:
        for schema in ("fact", "plan", "ai", "ops", "identity"):
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    # Apply the real migrations in-process, so this exercises what actually ships
    # without depending on a subprocess inheriting the right environment.
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    with mock.patch.dict(os.environ, {"HC_DATABASE_URL": settings.database_url}):
        get_settings.cache_clear()
        command.upgrade(alembic_config, "head")
    get_settings.cache_clear()

    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    from sqlalchemy.orm import sessionmaker

    from healthcurve.api import deps
    from healthcurve.app import create_app

    factory = sessionmaker(engine, expire_on_commit=False)

    with factory() as session, session.begin():
        session.add(
            Owner(
                email=EMAIL,
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="Europe/London",
            )
        )

    def override() -> Iterator[Any]:
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app = create_app(settings)
    app.dependency_overrides[deps.session_scope] = override
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {auth.CSRF_HEADER_NAME: response.json()["csrf_token"]}


def _complete_private_export(
    client: TestClient,
    headers: dict[str, str],
    engine: Engine,
    settings: Settings,
    *,
    key: str,
    include_ai: bool = False,
    include_sensitive: bool = True,
):
    queued = client.post(
        "/api/v1/privacy/export",
        headers={**headers, "Idempotency-Key": key},
        json={
            "password": PASSWORD,
            "include_ai": include_ai,
            "include_sensitive": include_sensitive,
        },
    )
    assert queued.status_code == 202, queued.text
    factory = sessionmaker(engine, expire_on_commit=False)
    claimed = queue_worker.run_once(
        factory,
        {
            PRIVATE_EXPORT_TASK: make_generation_handler(
                factory,
                root=settings.report_artifacts_dir,
                uploads=DocumentLayout(settings.uploads_dir),
            )
        },
        worker_id=f"test-export-{key}",
    )
    assert claimed is not None
    status_response = client.get(f"/api/v1/privacy/exports/{queued.json()['id']}")
    assert status_response.status_code == 200, status_response.text
    assert status_response.json()["status"] == "completed"
    downloaded = client.get(status_response.json()["download_url"])
    assert downloaded.status_code == 200, downloaded.text
    assert downloaded.headers["cache-control"] == "no-store"
    return queued, status_response, downloaded


# ---------------------------------------------------------------------------
# Authentication and CSRF
# ---------------------------------------------------------------------------


def test_migrated_restore_sentinel_matches_exactly(engine: Engine) -> None:
    """The real PostgreSQL migration installs the canary consumed by restore drills."""

    assert assert_restore_sentinel(engine) is None


def test_restore_sentinel_migration_downgrades_and_reinstalls(
    engine: Engine, settings: Settings
) -> None:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "f6d81a2c4b90",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('ops.restore_sentinel')")) is None
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()
    assert assert_restore_sentinel(engine) is None


def test_private_export_migration_downgrades_and_reinstalls(
    engine: Engine, settings: Settings
) -> None:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "ab3d5f7a9c21",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.connect() as connection:
            assert connection.scalar(text("SELECT to_regclass('ops.private_export')")) is None
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT to_regclass('ops.private_export')")) == (
            "ops.private_export"
        )


def test_regimen_time_migration_marks_legacy_rows_ambiguous(
    engine: Engine, settings: Settings
) -> None:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    legacy_id = uuid.uuid4()
    legacy_owner_id = uuid.uuid4()
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "a81d4f6c2e90",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO identity.owner "
                    "(id, email, password_hash, default_timezone, locale, "
                    "failed_login_count, mfa_enabled) VALUES "
                    "(:id, :email, 'synthetic-non-login-hash', 'UTC', 'en-GB', 0, false)"
                ),
                {"id": legacy_owner_id, "email": f"legacy-{legacy_owner_id}@example.test"},
            )
            connection.execute(
                text(
                    "INSERT INTO plan.regimen_version "
                    "(id, owner_id, version_label, status, effective_from, effective_to, "
                    "effective_period) VALUES "
                    "(:id, :owner_id, 'Synthetic legacy plan', 'draft', "
                    "'2020-01-01 09:00:00', NULL, "
                    "tsrange('2020-01-01 09:00:00', NULL, '[)'))"
                ),
                {"id": legacy_id, "owner_id": legacy_owner_id},
            )
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()

    with engine.begin() as connection:
        row = connection.execute(
            text(
                "SELECT effective_timezone, effective_from_local, "
                "effective_from_utc_offset_minutes, effective_time_provenance "
                "FROM plan.regimen_version WHERE id = :id"
            ),
            {"id": legacy_id},
        ).one()
        assert row == (None, None, None, "legacy_naive_utc_ambiguous")
        connection.execute(
            text("DELETE FROM plan.regimen_version WHERE id = :id"), {"id": legacy_id}
        )
        connection.execute(
            text("DELETE FROM identity.owner WHERE id = :id"), {"id": legacy_owner_id}
        )


def test_garmin_sync_origin_migration_marks_existing_rows_legacy(
    engine: Engine, settings: Settings
) -> None:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    legacy_id = uuid.uuid4()
    legacy_owner_id = uuid.uuid4()
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "0c9e4b7a1d23",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO identity.owner "
                    "(id, email, password_hash, default_timezone, locale, "
                    "failed_login_count, mfa_enabled) VALUES "
                    "(:id, :email, 'synthetic-non-login-hash', 'UTC', 'en-GB', 0, false)"
                ),
                {"id": legacy_owner_id, "email": f"legacy-{legacy_owner_id}@example.test"},
            )
            connection.execute(
                text(
                    "INSERT INTO ops.garmin_sync_run "
                    "(id, owner_id, requested_start_date, requested_end_date, timezone, "
                    "status, started_at, finished_at, counts, warning_codes, client_version) "
                    "VALUES (:id, :owner_id, '2026-08-10', '2026-08-11', 'UTC', "
                    "'completed', '2026-08-11T08:00:00Z', '2026-08-11T08:01:00Z', "
                    "'{}'::jsonb, '[]'::jsonb, 'synthetic')"
                ),
                {"id": legacy_id, "owner_id": legacy_owner_id},
            )
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()

    with engine.begin() as connection:
        assert (
            connection.scalar(
                text("SELECT origin FROM ops.garmin_sync_run WHERE id = :id"),
                {"id": legacy_id},
            )
            == "legacy"
        )
        connection.execute(
            text("DELETE FROM ops.garmin_sync_run WHERE id = :id"), {"id": legacy_id}
        )
        connection.execute(
            text("DELETE FROM identity.owner WHERE id = :id"), {"id": legacy_owner_id}
        )


def test_garmin_aggregate_index_migration_downgrades_and_reinstalls(
    engine: Engine, settings: Settings
) -> None:
    """Upgrade from the prior schema installs the selective long-history index."""
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    query = text(
        "SELECT indexdef FROM pg_indexes WHERE schemaname='fact' "
        "AND tablename='garmin_metric_event' "
        "AND indexname='ix_garmin_metric_owner_aggregate_occurred'"
    )
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "6f1c2a8d4b90",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.connect() as connection:
            assert connection.scalar(query) is None
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()

    with engine.connect() as connection:
        index_definition = connection.scalar(query)
    assert index_definition is not None
    assert "owner_id, occurred_at DESC, id" in index_definition
    assert "aggregation" in index_definition
    assert "<> 'provider_sample'" in index_definition


def test_cortisol_pk_parameter_migration_downgrades_and_reinstalls(
    engine: Engine, settings: Settings
) -> None:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "a7c3e9d1f620",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT to_regclass('ops.cortisol_pk_parameter_revision')"))
                is None
            )
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()

    with engine.connect() as connection:
        assert connection.scalar(
            text("SELECT to_regclass('ops.cortisol_pk_parameter_revision')")
        ) == ("ops.cortisol_pk_parameter_revision")


def test_wearable_summary_migration_downgrades_and_reinstalls(
    engine: Engine, settings: Settings
) -> None:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "9a2c4e6f8b10",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.connect() as connection:
            assert (
                connection.scalar(text("SELECT to_regclass('ops.wearable_daily_summary')")) is None
            )
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()

    with engine.connect() as connection:
        assert (
            connection.scalar(text("SELECT to_regclass('ops.wearable_daily_summary')"))
            == "ops.wearable_daily_summary"
        )
        triggers = set(
            connection.scalars(
                text(
                    "SELECT trigger_name FROM information_schema.triggers "
                    "WHERE event_object_schema='fact' "
                    "AND event_object_table='garmin_metric_event'"
                )
            )
        )
    assert {
        "invalidate_wearable_daily_summary_after_insert",
        "invalidate_wearable_daily_summary_after_delete",
    } <= triggers


def test_meal_event_migration_downgrades_and_reinstalls(engine: Engine, settings: Settings) -> None:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    table_query = text("SELECT to_regclass('fact.meal_event')")
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "e4c7a1b9d260",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.connect() as connection:
            assert connection.scalar(table_query) is None
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()

    with engine.connect() as connection:
        assert connection.scalar(table_query) == "fact.meal_event"


def test_symptom_category_and_posture_migration_preserves_unknown_as_nullable(
    engine: Engine, settings: Settings
) -> None:
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    environment = {"HC_DATABASE_URL": settings.database_url}
    columns_query = text(
        "SELECT table_name, column_name, is_nullable "
        "FROM information_schema.columns "
        "WHERE table_schema = 'fact' AND ("
        "(table_name = 'symptom_event' AND column_name IN "
        "('tracking_category', 'tracking_category_revision')) OR "
        "(table_name = 'blood_pressure_event' AND column_name = 'body_position')) "
        "ORDER BY table_name, column_name"
    )
    try:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.downgrade(
                alembic_config,
                "f9b2c4d6e810",  # pragma: allowlist secret - Alembic revision ID
            )
        with engine.connect() as connection:
            assert list(connection.execute(columns_query)) == []
    finally:
        with mock.patch.dict(os.environ, environment):
            get_settings.cache_clear()
            command.upgrade(alembic_config, "head")
        get_settings.cache_clear()

    with engine.connect() as connection:
        assert [tuple(row) for row in connection.execute(columns_query)] == [
            ("blood_pressure_event", "body_position", "YES"),
            ("symptom_event", "tracking_category", "YES"),
            ("symptom_event", "tracking_category_revision", "YES"),
        ]


def test_health_data_requires_authentication(client: TestClient) -> None:
    client.cookies.clear()
    for path in (
        "/api/v1/doses",
        "/api/v1/timeline",
        "/api/v1/medications",
        "/api/v1/labs/results",
        "/api/v1/labs/documents",
        "/api/v1/labs/documents/00000000-0000-0000-0000-000000000000",
        "/api/v1/labs/documents/00000000-0000-0000-0000-000000000000/pages/1/preview",
        "/api/v1/reports",
        "/api/v1/context-events",
        "/api/v1/blood-pressure",
        "/api/v1/weight",
        "/api/v1/analytics/steroid-exposure?day=2026-08-11&timezone=UTC",
        "/api/v1/analytics/daily-patterns?date_from=2026-08-11&date_to=2026-08-11&timezone=UTC",
        "/api/v1/analytics/daily-patterns.csv?date_from=2026-08-11&date_to=2026-08-11&timezone=UTC",
    ):
        assert client.get(path).status_code == 401, path


def test_anonymous_emergency_page_is_useful_without_disclosing_owner_data(
    client: TestClient,
) -> None:
    client.cookies.clear()
    response = client.get("/emergency")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "call your local emergency services" in body.lower()
    assert "medical id" in body.lower()
    assert "Private emergency details are locked" in body
    assert "physician-authored instructions</h2>" not in body
    assert "Log an emergency injection" not in body
    assert "<form" not in body
    assert (
        client.post(
            "/emergency/injection",
            data={"medication_id": str(uuid.uuid4()), "amount": "100"},
        ).status_code
        == 401
    )


def test_polling_mode_does_not_expose_the_telegram_webhook(client: TestClient) -> None:
    """ADR-0008: the default outbound transport has no inbound integration route."""
    client.cookies.clear()
    assert client.post("/api/v1/integrations/telegram/webhook", json={}).status_code == 404


def test_login_does_not_reveal_whether_an_account_exists(
    client: TestClient, engine: Engine
) -> None:
    client.cookies.clear()
    with Session(engine) as session:
        audit_count_before = (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(AuditEntry.action == AuditAction.LOGIN_FAILED)
            )
            or 0
        )
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x" * 12}
    )
    wrong = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]
    with Session(engine) as session:
        entries = list(
            session.scalars(
                select(AuditEntry)
                .where(AuditEntry.action == AuditAction.LOGIN_FAILED)
                .order_by(AuditEntry.occurred_at.desc())
                .limit(2)
            )
        )
        assert len(entries) == 2
        assert all(entry.actor == audit.UNAUTHENTICATED_ACTOR for entry in entries)
        assert all("example.com" not in entry.actor for entry in entries)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(AuditEntry.action == AuditAction.LOGIN_FAILED)
            )
            == audit_count_before + 2
        )


def test_failed_login_lockout_and_audit_persist_across_requests(
    client: TestClient, engine: Engine
) -> None:
    client.cookies.clear()
    with Session(engine) as session, session.begin():
        owner = session.scalar(select(Owner).where(Owner.email == EMAIL))
        assert owner is not None
        owner.failed_login_count = 0
        owner.locked_until = None
        unchanged_identity = (
            owner.email,
            owner.password_hash,
            owner.display_name,
            owner.default_timezone,
        )
        audit_count_before = (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(AuditEntry.action == AuditAction.LOGIN_FAILED)
            )
            or 0
        )

    application = cast(Any, client.app)
    original_telemetry = application.state.telemetry
    telemetry = mock.MagicMock()
    application.state.telemetry = telemetry
    try:
        for expected_count in range(1, auth.MAX_FAILED_LOGINS):
            rejected = client.post(
                "/api/v1/auth/login",
                json={"email": EMAIL, "password": PASSWORD + "-wrong"},
            )
            assert rejected.status_code == 401
            assert rejected.json() == {"detail": "invalid credentials"}
            with Session(engine) as session:
                owner = session.scalar(select(Owner).where(Owner.email == EMAIL))
                assert owner is not None
                assert owner.failed_login_count == expected_count
                assert owner.locked_until is None

        threshold = client.post(
            "/api/v1/auth/login",
            json={"email": EMAIL, "password": PASSWORD + "-wrong"},
        )
        locked = client.post(
            "/api/v1/auth/login",
            json={"email": EMAIL, "password": PASSWORD + "-wrong"},
        )
    finally:
        application.state.telemetry = original_telemetry

    assert threshold.status_code == 401
    assert threshold.json() == {"detail": "invalid credentials"}
    assert locked.status_code == 429
    assert locked.json() == {"detail": "too many failed attempts; try again later"}
    assert telemetry.record.call_count == auth.MAX_FAILED_LOGINS + 1
    telemetry.record.assert_called_with(OperationalEvent.AUTH_FAILURE)

    with Session(engine) as session, session.begin():
        owner = session.scalar(select(Owner).where(Owner.email == EMAIL))
        assert owner is not None
        assert owner.failed_login_count == 0
        assert owner.locked_until is not None
        assert owner.locked_until > datetime.now(UTC)
        assert (
            owner.email,
            owner.password_hash,
            owner.display_name,
            owner.default_timezone,
        ) == unchanged_identity
        entries = list(
            session.scalars(
                select(AuditEntry)
                .where(AuditEntry.action == AuditAction.LOGIN_FAILED)
                .order_by(AuditEntry.occurred_at.desc())
                .limit(auth.MAX_FAILED_LOGINS + 1)
            )
        )
        assert len(entries) == auth.MAX_FAILED_LOGINS + 1
        assert all(entry.actor == audit.UNAUTHENTICATED_ACTOR for entry in entries)
        assert all(EMAIL not in entry.actor for entry in entries)
        assert all(PASSWORD not in (entry.change_summary or "") for entry in entries)
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(AuditEntry.action == AuditAction.LOGIN_FAILED)
            )
            == audit_count_before + auth.MAX_FAILED_LOGINS + 1
        )

        owner.locked_until = datetime.now(UTC) - timedelta(seconds=1)

    recovered = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert recovered.status_code == 200
    with Session(engine) as session:
        owner = session.scalar(select(Owner).where(Owner.email == EMAIL))
        assert owner is not None
        assert owner.failed_login_count == 0
        assert owner.locked_until is None


def test_login_limit_returns_observable_429_before_authentication(client: TestClient) -> None:
    client.cookies.clear()
    app = cast(Any, client.app)
    original = app.state.rate_limiter
    limiter = mock.MagicMock(spec=RateLimiter)
    limiter.check.side_effect = RateLimitExceeded(RateLimitResult(5, 0, 81))
    app.state.rate_limiter = limiter
    try:
        response = client.post(
            "/api/v1/auth/login",
            # The limiter runs before authentication; reuse the synthetic fixture
            # rather than adding another password-like literal to the repository.
            json={"email": EMAIL, "password": PASSWORD},
        )
    finally:
        app.state.rate_limiter = original

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limit_exceeded"
    assert response.headers["retry-after"] == "81"
    assert response.headers["ratelimit-limit"] == "5"
    assert response.headers["ratelimit-remaining"] == "0"


def test_report_limit_returns_observable_429(client: TestClient, logged_in: dict[str, str]) -> None:
    app = cast(Any, client.app)
    original = app.state.rate_limiter
    limiter = mock.MagicMock(spec=RateLimiter)
    limiter.check.side_effect = RateLimitExceeded(RateLimitResult(6, 0, 120))
    app.state.rate_limiter = limiter
    try:
        response = client.post(
            "/api/v1/reports",
            headers=logged_in,
            json={
                "date_from": "2026-08-09",
                "date_to": "2026-08-09",
                "selected_sections": ["metrics"],
            },
        )
    finally:
        app.state.rate_limiter = original

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limit_exceeded"
    assert response.headers["retry-after"] == "120"


def test_chat_model_limit_returns_observable_429(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    created = client.post(
        "/api/v1/chat/conversations",
        headers=logged_in,
        json={"title": "Synthetic rate-limit check"},
    )
    assert created.status_code == 201
    conversation_id = created.json()["id"]
    app = cast(Any, client.app)
    original = app.state.rate_limiter
    limiter = mock.MagicMock(spec=RateLimiter)
    limiter.check.side_effect = RateLimitExceeded(RateLimitResult(30, 0, 45))
    app.state.rate_limiter = limiter
    try:
        response = client.post(
            f"/api/v1/chat/conversations/{conversation_id}/messages",
            headers=logged_in,
            json={"body": "Summarize the synthetic day.", "client_message_id": "limited-1"},
        )
    finally:
        app.state.rate_limiter = original

    assert response.status_code == 429
    assert response.json()["detail"]["code"] == "rate_limit_exceeded"
    assert response.headers["retry-after"] == "45"
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(ChatMessage)
                .where(ChatMessage.conversation_id == uuid.UUID(conversation_id))
            )
            == 0
        )
    assert (
        client.delete(
            f"/api/v1/chat/conversations/{conversation_id}", headers=logged_in
        ).status_code
        == 204
    )


def test_state_changing_requests_require_csrf(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    """A session cookie alone must never be enough to cause a write (T1)."""
    response = client.post("/api/v1/medications", json={"name": "x", "default_unit": "mg"})
    assert response.status_code == 403


def test_reads_do_not_require_csrf(client: TestClient, logged_in: dict[str, str]) -> None:
    assert client.get("/api/v1/medications").status_code == 200


def test_session_expiry_idle_timeout_and_logout_everywhere_revoke_access(
    client: TestClient, engine: Engine
) -> None:
    client.cookies.clear()
    first = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    second = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert first.status_code == 200
    assert second.status_code == 200
    first_token = first.cookies.get(auth.SESSION_COOKIE_NAME)
    second_token = second.cookies.get(auth.SESSION_COOKIE_NAME)
    assert first_token
    assert second_token

    revoked = client.post(
        "/api/v1/auth/logout-everywhere",
        headers={auth.CSRF_HEADER_NAME: second.json()["csrf_token"]},
    )
    assert revoked.status_code == 204
    for token in (first_token, second_token):
        client.cookies.set(auth.SESSION_COOKIE_NAME, token)
        assert client.get("/api/v1/auth/me").status_code == 401

    with Session(engine) as session:
        assert session.scalar(
            select(func.count())
            .select_from(AuditEntry)
            .where(AuditEntry.action == AuditAction.SESSION_REVOKED)
        )

    expired = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    expired_token = expired.cookies.get(auth.SESSION_COOKIE_NAME)
    assert expired_token
    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        expired_session = session.scalar(
            select(AuthSession)
            .where(AuthSession.owner_id == owner_id)
            .order_by(AuthSession.created_at.desc())
            .limit(1)
        )
        assert expired_session is not None
        expired_session.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    client.cookies.set(auth.SESSION_COOKIE_NAME, expired_token)
    assert client.get("/api/v1/auth/me").status_code == 401

    idle = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    idle_token = idle.cookies.get(auth.SESSION_COOKIE_NAME)
    assert idle_token
    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        idle_session = session.scalar(
            select(AuthSession)
            .where(AuthSession.owner_id == owner_id)
            .order_by(AuthSession.created_at.desc())
            .limit(1)
        )
        assert idle_session is not None
        idle_session.last_seen_at = (
            datetime.now(UTC) - auth.SESSION_IDLE_TIMEOUT - timedelta(seconds=1)
        )
    client.cookies.set(auth.SESSION_COOKIE_NAME, idle_token)
    assert client.get("/api/v1/auth/me").status_code == 401
    client.cookies.clear()


def test_vitals_are_owner_scoped_correctable_and_exported(
    client: TestClient, logged_in: dict[str, str], engine: Engine, settings: Settings
) -> None:
    other_owner_id: uuid.UUID
    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        other = Owner(
            email="vitals-owner@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(other)
        session.flush()
        other_owner_id = other.id
        event_time = from_instant(datetime(2026, 8, 9, 6, tzinfo=UTC), "UTC")
        events.create_event(
            session,
            BloodPressureEvent,
            owner_id=other.id,
            event_time=event_time,
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            systolic_mmhg=499,
            diastolic_mmhg=1,
        )
        events.create_event(
            session,
            WeightEvent,
            owner_id=other.id,
            event_time=event_time,
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            value=Decimal("1"),
            unit=WeightUnit.KG,
            normalized_kg=Decimal("1.0000"),
        )
        events.create_event(
            session,
            TemperatureEvent,
            owner_id=other.id,
            event_time=event_time,
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            value=Decimal("98.6"),
            unit=TemperatureUnit.FAHRENHEIT,
            normalized_c=Decimal("37.00"),
        )

    event_time_payload = {
        "local_time": "2026-08-09T08:15:00",
        "timezone": "Europe/London",
    }
    no_csrf = client.post(
        "/api/v1/blood-pressure",
        json={"systolic_mmhg": 120, "diastolic_mmhg": 80, "time": event_time_payload},
    )
    assert no_csrf.status_code == 403
    invalid_setting = client.post(
        "/api/v1/blood-pressure",
        headers=logged_in,
        json={
            "systolic_mmhg": 120,
            "diastolic_mmhg": 80,
            "measurement_setting": "gym",
            "time": event_time_payload,
        },
    )
    assert invalid_setting.status_code == 422
    invalid_position = client.post(
        "/api/v1/blood-pressure",
        headers=logged_in,
        json={
            "systolic_mmhg": 120,
            "diastolic_mmhg": 80,
            "body_position": "walking",
            "time": event_time_payload,
        },
    )
    assert invalid_position.status_code == 422

    bp_response = client.post(
        "/api/v1/blood-pressure",
        headers=logged_in,
        json={
            "systolic_mmhg": 120,
            "diastolic_mmhg": 80,
            "pulse_bpm": 62,
            "body_position": "standing",
            "time": event_time_payload,
            "notes": "Synthetic cuff reading",
        },
    )
    assert bp_response.status_code == 201, bp_response.text
    bp = bp_response.json()
    assert bp["category"] == "fact"
    assert bp["measurement_setting"] == "home"
    assert bp["body_position"] == "standing"
    assert bp["time"]["occurred_at"] == "2026-08-09T07:15:00Z"
    assert bp["provenance"]["source_type"] == "web"

    weight_response = client.post(
        "/api/v1/weight",
        headers=logged_in,
        json={
            "value": "180",
            "unit": "lb",
            "measurement_setting": "provider",
            "time": event_time_payload,
        },
    )
    assert weight_response.status_code == 201, weight_response.text
    weight = weight_response.json()
    assert weight["value"] == "180.0000"
    assert weight["unit"] == "lb"
    assert weight["normalized_kg"] == "81.6466"
    assert weight["display_lb"] == "180.0"
    assert weight["measurement_setting"] == "provider"

    assert (
        client.post(
            "/api/v1/temperature",
            json={"value": "98.6", "unit": "f", "time": event_time_payload},
        ).status_code
        == 403
    )
    invalid_temperature = client.post(
        "/api/v1/temperature",
        headers=logged_in,
        json={"value": "200", "unit": "f", "time": event_time_payload},
    )
    assert invalid_temperature.status_code == 422
    temperature_response = client.post(
        "/api/v1/temperature",
        headers=logged_in,
        json={"value": "38", "unit": "c", "time": event_time_payload, "notes": "Synthetic"},
    )
    assert temperature_response.status_code == 201, temperature_response.text
    temperature = temperature_response.json()
    assert temperature["value"] == "38.00"
    assert temperature["unit"] == "c"
    assert temperature["normalized_c"] == "38.00"
    assert temperature["display_f"] == "100.4"
    assert temperature["display_c"] == "38.0"
    assert temperature["time"]["occurred_at"] == "2026-08-09T07:15:00Z"

    bp_correction = client.post(
        f"/api/v1/blood-pressure/{bp['id']}/correct",
        headers=logged_in,
        json={
            "reason": "Synthetic transcription correction",
            "changes": {
                "systolic_mmhg": 118,
                "pulse_bpm": None,
                "measurement_setting": "provider",
                "body_position": "sitting",
            },
        },
    )
    assert bp_correction.status_code == 201, bp_correction.text
    corrected_bp = bp_correction.json()
    assert corrected_bp["systolic_mmhg"] == 118
    assert corrected_bp["pulse_bpm"] is None
    assert corrected_bp["measurement_setting"] == "provider"
    assert corrected_bp["body_position"] == "sitting"
    assert corrected_bp["provenance"]["supersedes_id"] == bp["id"]

    weight_correction = client.post(
        f"/api/v1/weight/{weight['id']}/correct",
        headers=logged_in,
        json={
            "reason": "Synthetic unit correction",
            "changes": {"value": "82", "unit": "kg", "measurement_setting": "home"},
        },
    )
    assert weight_correction.status_code == 201, weight_correction.text
    corrected_weight = weight_correction.json()
    assert corrected_weight["normalized_kg"] == "82.0000"
    assert corrected_weight["display_lb"] == "180.8"
    assert corrected_weight["measurement_setting"] == "home"

    temperature_correction = client.post(
        f"/api/v1/temperature/{temperature['id']}/correct",
        headers=logged_in,
        json={"reason": "Synthetic unit correction", "changes": {"value": "98.6", "unit": "f"}},
    )
    assert temperature_correction.status_code == 201, temperature_correction.text
    corrected_temperature = temperature_correction.json()
    assert corrected_temperature["display_f"] == "98.6"
    assert corrected_temperature["display_c"] == "37.0"
    assert corrected_temperature["provenance"]["supersedes_id"] == temperature["id"]

    current_bp = client.get("/api/v1/blood-pressure").json()
    current_weight = client.get("/api/v1/weight").json()
    current_temperature = client.get("/api/v1/temperature").json()
    assert {row["id"] for row in current_bp["items"]} == {corrected_bp["id"]}
    assert {row["id"] for row in current_weight["items"]} == {corrected_weight["id"]}
    assert {row["id"] for row in current_bp["revisions"]} == {bp["id"]}
    assert {row["id"] for row in current_temperature["items"]} == {corrected_temperature["id"]}
    assert {row["id"] for row in current_temperature["revisions"]} == {temperature["id"]}

    timeline = client.get("/api/v1/timeline", params={"types": "blood_pressure,weight,temperature"})
    assert timeline.status_code == 200, timeline.text
    items = timeline.json()["items"]
    assert {item["event_type"] for item in items} == {
        "blood_pressure",
        "weight",
        "temperature",
    }
    assert any(item["summary"] == "Blood pressure 118/80 mmHg · provider" for item in items)
    assert any(item["summary"] == "Weight 180.8 lb (entered 82.0000 kg) · home" for item in items)
    assert any(item["summary"] == "Temperature 98.6 °F (37.0 °C)" for item in items)

    _, _, exported = _complete_private_export(
        client, logged_in, engine, settings, key="vitals-export"
    )
    facts = exported.json()["facts"]
    assert corrected_bp["id"] in {row["id"] for row in facts["blood_pressure"]}
    assert corrected_weight["id"] in {row["id"] for row in facts["weight"]}
    assert (
        next(row for row in facts["blood_pressure"] if row["id"] == corrected_bp["id"])[
            "measurement_setting"
        ]
        == "provider"
    )
    assert (
        next(row for row in facts["weight"] if row["id"] == corrected_weight["id"])[
            "measurement_setting"
        ]
        == "home"
    )
    assert corrected_temperature["id"] in {row["id"] for row in facts["temperature"]}

    with Session(engine) as session, session.begin():
        other_bp = session.scalar(
            select(BloodPressureEvent).where(BloodPressureEvent.owner_id == other_owner_id)
        )
        other_weight = session.scalar(
            select(WeightEvent).where(WeightEvent.owner_id == other_owner_id)
        )
        assert other_bp is not None and other_bp.measurement_setting.value == "home"
        assert other_weight is not None and other_weight.measurement_setting.value == "home"
        session.execute(
            text("DELETE FROM fact.blood_pressure_event WHERE owner_id = :owner_id"),
            {"owner_id": other_owner_id},
        )
        session.execute(
            text("DELETE FROM fact.weight_event WHERE owner_id = :owner_id"),
            {"owner_id": other_owner_id},
        )
        session.execute(
            text("DELETE FROM fact.temperature_event WHERE owner_id = :owner_id"),
            {"owner_id": other_owner_id},
        )
        session.delete(session.get(Owner, other_owner_id))


def test_context_privacy_time_provenance_corrections_and_deletion(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    hidden_id: uuid.UUID
    secondary_id: uuid.UUID
    observed_at = datetime.now(UTC)
    with Session(engine) as session, session.begin():
        secondary = Owner(
            email="context-owner@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(secondary)
        session.flush()
        secondary_id = secondary.id
        hidden = ContextEvent(
            owner_id=secondary.id,
            occurred_at=observed_at,
            local_time=observed_at.replace(tzinfo=None),
            timezone="UTC",
            utc_offset_minutes=0,
            recorded_at=observed_at,
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            location_precision=LocationPrecision.NONE,
            exact_location_consent=False,
        )
        session.add(hidden)
        session.flush()
        hidden_id = hidden.id

    coarse_payload = {
        "time": {"local_time": "2026-03-29T08:15:00", "timezone": "Europe/London"},
        "location_precision": "coarse",
        "coarse_location_label": "Central London",
        "latitude": "51.5",
        "longitude": "-0.1",
        "weather_provider": "manual",
        "weather_observed_at": "2026-03-29T07:15:00Z",
        "temperature": "12.50",
        "temperature_unit": "c",
        "pressure": "1012.30",
        "pressure_unit": "hpa",
        "humidity_percent": "72.00",
        "conditions": "Synthetic overcast",
    }
    without_csrf = client.post("/api/v1/context-events", json=coarse_payload)
    assert without_csrf.status_code == 403

    missing_consent = client.post(
        "/api/v1/context-events",
        headers=logged_in,
        json={
            "time": {"local_time": "2026-03-29T08:15:00", "timezone": "Europe/London"},
            "location_precision": "exact",
            "latitude": "51.507400",
            "longitude": "-0.127800",
        },
    )
    assert missing_consent.status_code == 422

    naive_weather_time = client.post(
        "/api/v1/context-events",
        headers=logged_in,
        json={**coarse_payload, "weather_observed_at": "2026-03-29T07:15:00"},
    )
    assert naive_weather_time.status_code == 422

    overly_precise_coarse = client.post(
        "/api/v1/context-events",
        headers=logged_in,
        json={**coarse_payload, "latitude": "51.5074", "longitude": "-0.1278"},
    )
    assert overly_precise_coarse.status_code == 422

    created = client.post("/api/v1/context-events", headers=logged_in, json=coarse_payload)
    assert created.status_code == 201, created.text
    original = created.json()
    assert original["time"] == {
        "occurred_at": "2026-03-29T07:15:00Z",
        "local_time": "2026-03-29T08:15:00",
        "timezone": "Europe/London",
        "utc_offset_minutes": 60,
    }
    assert original["coarse_location_label"] == "Central London"
    assert original["latitude"] == "51.5"
    assert original["weather_provider"] == "manual"
    assert original["temperature"] == "12.50"

    diary = client.post(
        "/api/v1/diary-events",
        headers=logged_in,
        json={
            "text": "Synthetic fact independent of context",
            "time": {"local_time": "2026-03-29T08:20:00", "timezone": "Europe/London"},
        },
    )
    assert diary.status_code == 201, diary.text
    diary_id = diary.json()["id"]

    corrected = client.post(
        f"/api/v1/context-events/{original['id']}/correct",
        headers=logged_in,
        json={
            "reason": "Synthetic travel correction",
            "replacement": {
                "time": {"local_time": "2026-03-30T18:00:00", "timezone": "Asia/Tokyo"},
                "location_precision": "exact",
                "latitude": "35.676200",
                "longitude": "139.650300",
                "exact_location_consent": True,
                "notes": "Synthetic exact-location consent fixture",
            },
        },
    )
    assert corrected.status_code == 201, corrected.text
    replacement = corrected.json()
    assert replacement["time"]["occurred_at"] == "2026-03-30T09:00:00Z"
    assert replacement["time"]["utc_offset_minutes"] == 540
    assert replacement["provenance"]["supersedes_id"] == original["id"]
    assert replacement["latitude"] == "35.676200"

    context_timeline = client.get(
        "/api/v1/timeline",
        params={"types": "context", "timezone": "Asia/Tokyo"},
    )
    assert context_timeline.status_code == 200, context_timeline.text
    context_item = next(
        item for item in context_timeline.json()["items"] if item["id"] == replacement["id"]
    )
    assert context_item["event_type"] == "context"
    assert context_item["summary"] == "Exact location recorded (consent on file)"
    assert "35.676200" not in context_item["summary"]

    current = client.get("/api/v1/context-events").json()
    assert replacement["id"] in {row["id"] for row in current["items"]}
    assert str(hidden_id) not in {row["id"] for row in current["items"]}
    assert original["id"] not in {row["id"] for row in current["items"]}
    assert {row["id"] for row in current["revisions"]} == {original["id"]}
    with Session(engine) as session:
        original_row = session.get(ContextEvent, uuid.UUID(original["id"]))
        assert original_row is not None
        assert original_row.coarse_location_label == "Central London"
        assert original_row.latitude == Decimal("51.500000")

    path = f"/api/v1/context-events/{replacement['id']}"
    wrong = client.request("DELETE", path, headers=logged_in, json={"password": "wrong-password"})
    assert wrong.status_code == 403
    deleted = client.request("DELETE", path, headers=logged_in, json={"password": PASSWORD})
    assert deleted.status_code == 204, deleted.text
    remaining_ids = {row["id"] for row in client.get("/api/v1/context-events").json()["items"]}
    assert original["id"] not in remaining_ids
    assert replacement["id"] not in remaining_ids
    assert diary_id in {row["id"] for row in client.get("/api/v1/diary-events").json()["items"]}
    with Session(engine) as session:
        entry = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == AuditAction.RECORD_DELETED,
                AuditEntry.target_id == uuid.UUID(original["id"]),
            )
        )
        assert entry is not None
        assert entry.change_summary == "deleted context correction chain (2 revisions)"
        session.delete(session.get(ContextEvent, hidden_id))
        session.delete(session.get(Owner, secondary_id))
        session.commit()


def test_individual_deletion_requires_password_and_preserves_audit(
    client: TestClient, logged_in: dict[str, str], engine: Engine, settings: Settings
) -> None:
    created = client.post(
        "/api/v1/diary-events",
        headers=logged_in,
        json={
            "text": "Synthetic deletion fixture",
            "time": {"local_time": "2026-08-09T11:00:00", "timezone": "Europe/London"},
        },
    )
    assert created.status_code == 201, created.text
    record_id = created.json()["id"]
    path = f"/api/v1/privacy/records/diary/{record_id}"
    wrong = client.request("DELETE", path, headers=logged_in, json={"password": "wrong"})
    assert wrong.status_code == 403
    assert any(row["id"] == record_id for row in client.get("/api/v1/diary-events").json()["items"])

    deleted = client.request("DELETE", path, headers=logged_in, json={"password": PASSWORD})
    assert deleted.status_code == 204, deleted.text
    assert all(row["id"] != record_id for row in client.get("/api/v1/diary-events").json()["items"])
    with Session(engine) as session:
        entry = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == AuditAction.RECORD_DELETED,
                AuditEntry.target_id == uuid.UUID(record_id),
            )
        )
        assert entry is not None
        assert entry.change_summary == "physical deletion"

    symptom = client.post(
        "/api/v1/symptoms",
        headers=logged_in,
        json={
            "name": "Synthetic correction history",
            "time": {"local_time": "2026-08-09T11:05:00", "timezone": "Europe/London"},
        },
    ).json()
    corrected = client.post(
        f"/api/v1/symptoms/{symptom['id']}/correct",
        headers=logged_in,
        json={"reason": "Synthetic correction", "changes": {"notes": "Synthetic note"}},
    )
    assert corrected.status_code == 201, corrected.text
    protected = client.request(
        "DELETE",
        f"/api/v1/privacy/records/symptom/{corrected.json()['id']}",
        headers=logged_in,
        json={"password": PASSWORD},
    )
    assert protected.status_code == 409

    bad_confirmation = client.request(
        "DELETE",
        "/api/v1/privacy/account",
        headers=logged_in,
        json={"password": PASSWORD, "confirmation": "delete"},
    )
    assert bad_confirmation.status_code == 422
    assert client.get("/api/v1/auth/me").status_code == 200

    wrong_export = client.post(
        "/api/v1/privacy/export",
        headers={**logged_in, "Idempotency-Key": "wrong-password-export"},
        json={"password": PASSWORD + "-wrong"},
    )
    assert wrong_export.status_code == 403
    _, export_status, private_export = _complete_private_export(
        client, logged_in, engine, settings, key="individual-deletion-export"
    )
    assert private_export.status_code == 200
    assert "attachment" in private_export.headers["content-disposition"]
    assert private_export.json()["ai"] == {}
    assert export_status.json()["progress_percent"] == 100.0


def test_full_export_requires_csrf_and_password_reauthentication(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    with Session(engine) as session:
        audit_count_before = (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(AuditEntry.action == AuditAction.EXPORT_GENERATED)
            )
            or 0
        )

    missing_csrf = client.post("/api/v1/privacy/export", json={"password": PASSWORD})
    wrong_csrf = client.post(
        "/api/v1/privacy/export",
        headers={auth.CSRF_HEADER_NAME: "not-this-session-token"},
        json={"password": PASSWORD},
    )
    wrong_password = client.post(
        "/api/v1/privacy/export",
        headers={**logged_in, "Idempotency-Key": "wrong-password-full-export"},
        json={"password": PASSWORD + "-wrong"},
    )
    legacy = client.post("/api/v1/exports", headers=logged_in)
    missing_idempotency = client.post(
        "/api/v1/privacy/export", headers=logged_in, json={"password": PASSWORD}
    )

    assert missing_csrf.status_code == 403
    assert wrong_csrf.status_code == 403
    assert wrong_password.status_code == 403
    assert legacy.status_code == 404
    assert missing_idempotency.status_code == 422
    for failed in (missing_csrf, wrong_csrf, wrong_password, legacy, missing_idempotency):
        assert "facts" not in failed.text
        assert "plan" not in failed.text

    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(AuditEntry.action == AuditAction.EXPORT_GENERATED)
            )
            == audit_count_before
        )

    queued, status_response, exported = _complete_private_export(
        client, logged_in, engine, settings, key="full-export-reauth"
    )
    assert queued.status_code == 202
    assert status_response.json()["attempt_count"] == 1
    assert exported.status_code == 200
    assert exported.headers["cache-control"] == "no-store"
    assert set(exported.json()) >= {"plan", "facts", "ai"}

    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(AuditEntry.action == AuditAction.EXPORT_GENERATED)
            )
            == audit_count_before + 1
        )


def test_private_export_idempotency_retry_progress_expiration_and_cleanup(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    key = "durable-export-lifecycle"
    request_headers = {**logged_in, "Idempotency-Key": key}
    first = client.post(
        "/api/v1/privacy/export",
        headers=request_headers,
        json={"password": PASSWORD, "include_sensitive": False},
    )
    replay = client.post(
        "/api/v1/privacy/export",
        headers=request_headers,
        json={"password": PASSWORD, "include_sensitive": False},
    )
    conflict = client.post(
        "/api/v1/privacy/export",
        headers=request_headers,
        json={"password": PASSWORD, "include_sensitive": True},
    )
    assert first.status_code == replay.status_code == 202
    assert first.json()["id"] == replay.json()["id"]
    assert conflict.status_code == 409
    export_id = uuid.UUID(first.json()["id"])

    history = client.get("/api/v1/privacy/exports?page=1&page_size=10")
    assert history.status_code == 200
    assert export_id in {uuid.UUID(row["id"]) for row in history.json()["items"]}
    assert client.get(f"/api/v1/privacy/exports/{uuid.uuid4()}").status_code == 404

    other_owner_id: uuid.UUID
    other_export_id: uuid.UUID
    other_job_id: uuid.UUID
    with Session(engine) as session, session.begin():
        other = Owner(
            email="private-export-other@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(other)
        session.flush()
        other_owner_id = other.id
        other_request = request_export(
            session,
            owner_id=other_owner_id,
            idempotency_key="other-owner-export",
            include_ai=False,
            include_sensitive=True,
        )
        other_export_id = other_request.export.id
        other_job_id = other_request.job.id
    assert client.get(f"/api/v1/privacy/exports/{other_export_id}").status_code == 404
    assert client.get(f"/api/v1/privacy/exports/{other_export_id}/download").status_code == 404

    factory = sessionmaker(engine, expire_on_commit=False)

    def fail_safely(_session: Session, _payload: Any) -> None:
        raise JobQueueError("export_source_document_unavailable")

    failed = queue_worker.run_once(
        factory,
        {PRIVATE_EXPORT_TASK: fail_safely},
        worker_id="synthetic-export-failure",
    )
    assert failed is not None
    retry_status = client.get(f"/api/v1/privacy/exports/{export_id}")
    assert retry_status.json()["status"] == "queued"
    assert retry_status.json()["attempt_count"] == 1
    assert retry_status.json()["last_error_code"] == "export_source_document_unavailable"
    assert retry_status.json()["next_attempt_at"] is not None

    with Session(engine) as session, session.begin():
        export = session.get(PrivateExport, export_id)
        assert export is not None
        job = session.get(Job, export.job_id)
        assert job is not None
        job.run_at = datetime.now(UTC) - timedelta(seconds=1)
    completed = queue_worker.run_once(
        factory,
        {
            PRIVATE_EXPORT_TASK: make_generation_handler(
                factory,
                root=settings.report_artifacts_dir,
                uploads=DocumentLayout(settings.uploads_dir),
            )
        },
        worker_id="synthetic-export-retry",
    )
    assert completed is not None
    completed_status = client.get(f"/api/v1/privacy/exports/{export_id}").json()
    assert completed_status["status"] == "completed"
    assert completed_status["attempt_count"] == 2
    assert completed_status["progress_percent"] == 100.0
    artifact_path = settings.report_artifacts_dir / str(export_id)
    with Session(engine) as session, session.begin():
        export = session.get(PrivateExport, export_id)
        assert export is not None and export.relative_path is not None
        artifact_path = settings.report_artifacts_dir / export.relative_path
        export.created_at = datetime.now(UTC) - timedelta(days=8)
        export.expires_at = datetime.now(UTC) - timedelta(days=1)
    assert artifact_path.exists()
    assert client.get(f"/api/v1/privacy/exports/{export_id}/download").status_code == 409
    with Session(engine) as session, session.begin():
        make_cleanup_handler(settings.report_artifacts_dir)(session, {})
    assert not artifact_path.exists()
    with Session(engine) as session:
        export = session.get(PrivateExport, export_id)
        assert export is not None and export.purged_at is not None
    with Session(engine) as session, session.begin():
        other_job = session.get(Job, other_job_id)
        if other_job is not None:
            session.delete(other_job)
        other_owner = session.get(Owner, other_owner_id)
        if other_owner is not None:
            session.delete(other_owner)


def test_integration_deletion_removes_provider_data_and_audits(
    engine: Engine,
) -> None:
    with Session(engine) as session, session.begin():
        secondary = Owner(
            email="integration-delete@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(secondary)
        session.flush()
        owner_id = secondary.id
        session.add(
            GarminImportBatch(
                owner_id=owner_id,
                source_name="synthetic.fit",
                source_media_type="application/octet-stream",
                source_sha256="a" * 64,
                source_byte_size=1,
                source_payload=b"x",
                source_members=[],
                sdk_profile_version="synthetic",
                observed_metrics=[],
                missing_metrics=[],
                device_attributions=[],
            )
        )
        session.flush()
        result = privacy.delete_integration(
            session,
            owner_id=owner_id,
            provider="garmin",
            delete_data=True,
            telegram_chat_id=None,
        )
        assert result.data_rows == 1

    with Session(engine) as session:
        assert (
            session.scalar(select(GarminImportBatch).where(GarminImportBatch.owner_id == owner_id))
            is None
        )
        entry = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == AuditAction.INTEGRATION_DISCONNECTED,
                AuditEntry.actor == f"owner:{owner_id}",
            )
        )
        assert entry is not None


def test_telegram_disconnect_removes_short_lived_conversation_context(engine: Engine) -> None:
    with Session(engine) as session, session.begin():
        owner = Owner(
            email="telegram-context-delete@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(owner)
        session.flush()
        owner_id = owner.id
        session.add(
            TelegramConversationContext(
                owner_id=owner_id,
                chat_id=4242,
                turns=[],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.flush()

        result = privacy.delete_integration(
            session,
            owner_id=owner_id,
            provider="telegram",
            delete_data=False,
            telegram_chat_id=4242,
        )
        assert result.data_rows == 1

    with Session(engine) as session:
        assert (
            session.scalar(
                select(TelegramConversationContext).where(
                    TelegramConversationContext.owner_id == owner_id
                )
            )
            is None
        )


def test_weather_deletion_is_independent_of_the_coarse_location(engine: Engine) -> None:
    observed_at = datetime.now(UTC) - timedelta(minutes=5)
    with Session(engine) as session, session.begin():
        owner = Owner(
            email="weather-delete@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="America/New_York",
        )
        session.add(owner)
        session.flush()
        owner_id = owner.id
        source = events.create_event(
            session,
            ContextEvent,
            owner_id=owner_id,
            event_time=from_instant(observed_at, owner.default_timezone),
            source_type=SourceType.TELEGRAM,
            confirmation_state=ConfirmationState.CONFIRMED_FROM_DRAFT,
            location_precision=LocationPrecision.COARSE,
            coarse_location_label="Synthetic location",
            latitude=Decimal("40.7"),
            longitude=Decimal("-74.0"),
            exact_location_consent=False,
            provider_id="telegram-location:synthetic-weather-delete",
            source_revision="rounded-0.1-v1",
        )
        source_id = source.id
        events.create_event(
            session,
            ContextEvent,
            owner_id=owner_id,
            event_time=from_instant(observed_at, owner.default_timezone),
            source_type=SourceType.PROVIDER,
            confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
            location_precision=LocationPrecision.COARSE,
            coarse_location_label=source.coarse_location_label,
            latitude=source.latitude,
            longitude=source.longitude,
            exact_location_consent=False,
            weather_provider="open-meteo",
            weather_observation_id="synthetic-observation",
            weather_observed_at=observed_at,
            temperature=Decimal("20.0"),
            temperature_unit=ContextTemperatureUnit.CELSIUS,
            provider_id=f"open-meteo:{source.id}",
            source_revision="synthetic-weather-v1",
        )
        result = privacy.delete_integration(
            session,
            owner_id=owner_id,
            provider="weather",
            delete_data=True,
            telegram_chat_id=None,
        )
        assert result.credentials == 0
        assert result.data_rows == 1

    with Session(engine) as session:
        remaining = session.scalars(
            select(ContextEvent).where(ContextEvent.owner_id == owner_id)
        ).all()
        assert len(remaining) == 1
        assert remaining[0].id == source_id
        assert remaining[0].weather_provider is None


def test_account_deletion_service_removes_data_but_retains_structural_audit(
    engine: Engine, tmp_path: Path
) -> None:
    owner_id: uuid.UUID
    with Session(engine) as session, session.begin():
        secondary = Owner(
            email="delete-owner@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(secondary)
        session.flush()
        owner_id = secondary.id
        session.add(
            Medication(
                owner_id=owner_id,
                name="Synthetic deletion medicine",
                normalized_name="synthetic deletion medicine",
                default_unit=DoseUnit.MG,
                default_route=Route.ORAL,
            )
        )
        session.add(
            TelegramConversationContext(
                owner_id=owner_id,
                chat_id=4243,
                turns=[],
                expires_at=datetime.now(UTC) + timedelta(hours=1),
            )
        )
        session.add(
            ChatConversation(
                owner_id=owner_id,
                title="Synthetic account-deletion conversation",
            )
        )
        session.flush()
        privacy.delete_account(
            session,
            owner=secondary,
            uploads_dir=tmp_path,
            telegram_chat_id=None,
        )

    with Session(engine) as session:
        assert session.get(Owner, owner_id) is None
        assert session.scalar(select(Medication).where(Medication.owner_id == owner_id)) is None
        assert (
            session.scalar(
                select(TelegramConversationContext).where(
                    TelegramConversationContext.owner_id == owner_id
                )
            )
            is None
        )
        assert (
            session.scalar(select(ChatConversation).where(ChatConversation.owner_id == owner_id))
            is None
        )
        entry = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == AuditAction.DATA_DELETED,
                AuditEntry.target_id == owner_id,
            )
        )
        assert entry is not None
        assert entry.actor == f"owner:{owner_id}"
        assert "medicine" not in (entry.change_summary or "")


def test_episode_uses_validated_local_time_and_requires_end_when_closed(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    invalid = client.post(
        "/api/v1/stress-episodes",
        headers=logged_in,
        json={
            "trigger": "Synthetic invalid zone",
            "time": {"local_time": "2026-08-09T10:00:00", "timezone": "Not/AZone"},
        },
    )
    assert invalid.status_code == 422

    created = client.post(
        "/api/v1/stress-episodes",
        headers=logged_in,
        json={
            "trigger": "Synthetic illness",
            "severity": "moderate",
            "time": {"local_time": "2026-08-09T10:00:00", "timezone": "Europe/London"},
        },
    )
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["started_at"] == "2026-08-09T09:00:00Z"
    assert body["timezone"] == "Europe/London"

    missing_end = client.patch(
        f"/api/v1/stress-episodes/{body['id']}",
        headers=logged_in,
        json={"status": "resolved"},
    )
    assert missing_end.status_code == 422
    closed = client.patch(
        f"/api/v1/stress-episodes/{body['id']}",
        headers=logged_in,
        json={
            "status": "resolved",
            "ended_at": {
                "local_time": "2026-08-09T13:00:00",
                "timezone": "Europe/London",
            },
            "outcome": "Synthetic recovery",
        },
    )
    assert closed.status_code == 200, closed.text
    assert closed.json()["ended_at"] == "2026-08-09T12:00:00Z"
    assert closed.json()["outcome"] == "Synthetic recovery"


def test_garmin_preview_then_confirm_is_idempotent_and_preserves_provenance(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    fit = synthetic_fit()
    upload = {"file": ("synthetic.fit", fit, "application/vnd.ant.fit")}

    with Session(engine) as session:
        before = session.scalar(select(func.count()).select_from(GarminImportBatch))

    preview = client.post(
        "/api/v1/integrations/garmin/imports/preview",
        files=upload,
        data={"timezone": "Europe/London"},
        headers=logged_in,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["creates_facts"] is False
    assert {"activity", "heart_rate", "sleep", "sleep_score"} <= set(body["observed_metrics"])
    assert "stress" in body["missing_metrics"]

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GarminImportBatch)) == before
        assert session.scalar(select(func.count()).select_from(GarminMetricEvent)) == 0

    mismatch = client.post(
        "/api/v1/integrations/garmin/imports/confirm",
        files=upload,
        data={"timezone": "Europe/London", "expected_sha256": "0" * 64},
        headers=logged_in,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "preview_checksum_mismatch"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GarminImportBatch)) == before

    confirm_data = {
        "timezone": "Europe/London",
        "expected_sha256": body["source_sha256"],
    }
    first = client.post(
        "/api/v1/integrations/garmin/imports/confirm",
        files=upload,
        data=confirm_data,
        headers=logged_in,
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "batch_id": first.json()["batch_id"],
        "source_sha256": body["source_sha256"],
        "created": True,
        "metric_count": 4,
        "sleep_count": 1,
        "activity_count": 1,
    }

    second = client.post(
        "/api/v1/integrations/garmin/imports/confirm",
        files=upload,
        data=confirm_data,
        headers=logged_in,
    )
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["batch_id"] == first.json()["batch_id"]

    with Session(engine) as session:
        batch = session.scalar(
            select(GarminImportBatch).where(
                GarminImportBatch.source_sha256 == body["source_sha256"]
            )
        )
        assert batch is not None
        assert batch.source_payload == fit
        assert batch.source_sha256 == body["source_sha256"]
        assert session.scalar(select(func.count()).select_from(GarminImportBatch)) == 1
        assert session.scalar(select(func.count()).select_from(GarminMetricEvent)) == 4
        assert session.scalar(select(func.count()).select_from(GarminSleepEvent)) == 1
        assert session.scalar(select(func.count()).select_from(GarminSleepStageInterval)) == 1
        assert session.scalar(select(func.count()).select_from(GarminActivityEvent)) == 1
        metric = session.scalar(select(GarminMetricEvent))
        assert metric is not None
        assert metric.garmin_manufacturer == "garmin"
        assert metric.garmin_device_serial_hash
        assert metric.source_revision

    csv = synthetic_activity_csv()
    csv_upload = {"file": ("activities.csv", csv, "text/csv")}
    csv_preview = client.post(
        "/api/v1/integrations/garmin/imports/preview",
        files=csv_upload,
        data={"timezone": "Europe/London"},
        headers=logged_in,
    )
    assert csv_preview.status_code == 200, csv_preview.text
    assert csv_preview.json()["observed_metrics"] == ["activity"]
    csv_confirm = client.post(
        "/api/v1/integrations/garmin/imports/confirm",
        files=csv_upload,
        data={
            "timezone": "Europe/London",
            "expected_sha256": csv_preview.json()["source_sha256"],
        },
        headers=logged_in,
    )
    assert csv_confirm.status_code == 200, csv_confirm.text
    assert csv_confirm.json()["created"] is True
    assert csv_confirm.json()["activity_count"] == 1

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GarminImportBatch)) == 2
        assert session.scalar(select(func.count()).select_from(GarminActivityEvent)) == 2


def test_garmin_connect_sync_corrects_and_disconnects_owner_scoped_data(
    client: TestClient, engine: Engine, settings: Settings
) -> None:
    email = "garmin-owner@example.com"
    observed_at = datetime(2026, 1, 10, 12, tzinfo=UTC)
    with Session(engine) as session, session.begin():
        owner = Owner(
            email=email,
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(owner)
        session.flush()
        owner_id = owner.id
        session.add(
            GarminConnection(
                owner_id=owner_id,
                state=GarminConnectionState.CONNECTED,
                connected_at=observed_at,
                capabilities={},
                client_version="synthetic",
            )
        )

    def fetched(
        steps: int,
        heart_rate: int,
        nightly_hrv: int = 41,
        highest_respiration: float = 18.4,
        awake_minutes: int = 10,
    ) -> FetchedWindow:
        daily = map_day(
            day=date(2026, 1, 10),
            stats={"totalSteps": steps},
            sleep={
                "dailySleepDTO": {
                    "sleepStartTimestampGMT": int(
                        datetime(2026, 1, 9, 23, tzinfo=UTC).timestamp() * 1_000
                    ),
                    "sleepEndTimestampGMT": int(
                        datetime(2026, 1, 10, 7, tzinfo=UTC).timestamp() * 1_000
                    ),
                    "sleepStartTimestampLocal": int(
                        datetime(2026, 1, 9, 23, tzinfo=UTC).timestamp() * 1_000
                    ),
                    "timeZoneId": "UTC",
                    "awakeCount": 1,
                },
                "sleepLevels": [
                    {
                        "startGMT": "2026-01-09T23:00:00Z",
                        "endGMT": "2026-01-10T02:00:00Z",
                        "activityLevel": 1,
                    },
                    {
                        "startGMT": "2026-01-10T02:00:00Z",
                        "endGMT": f"2026-01-10T02:{awake_minutes:02d}:00Z",
                        "activityLevel": 3,
                    },
                    {
                        "startGMT": f"2026-01-10T02:{awake_minutes:02d}:00Z",
                        "endGMT": "2026-01-10T07:00:00Z",
                        "activityLevel": 1,
                    },
                ],
            },
            timezone="UTC",
        )
        assert daily.sleep is not None
        activities, activity_warnings = map_activities(
            [
                {
                    "activityId": 9001,
                    "activityType": {"typeKey": "walking"},
                    "activityName": "Synthetic walk",
                    "startTimeGMT": "2026-01-10T08:00:00Z",
                    "startTimeLocal": "2026-01-10T08:00:00",
                    "timeZoneId": "UTC",
                    "elapsedDuration": 1_800,
                    "distance": 1_609.344,
                }
            ],
            timezone="UTC",
        )
        start_ms = int(datetime(2026, 1, 10, 8, tzinfo=UTC).timestamp() * 1_000)
        intraday = map_intraday_day(
            day=date(2026, 1, 10),
            heart_rate={
                "heartRateValueDescriptors": [
                    {"index": 0, "key": "timestamp"},
                    {"index": 1, "key": "heartrate"},
                ],
                "heartRateValues": [[start_ms, heart_rate], [start_ms + 120_000, 74]],
            },
            stress={
                "stressValueDescriptorsDTOList": [
                    {"index": 0, "key": "timestamp"},
                    {"index": 1, "key": "stressLevel"},
                ],
                "stressValuesArray": [[start_ms + 180_000, 0]],
            },
            respiration={
                "respirationValueDescriptorsDTOList": [
                    {"index": 0, "key": "timestamp"},
                    {"index": 1, "key": "respiration"},
                ],
                "respirationValuesArray": [[start_ms + 240_000, 14.5]],
                "avgWakingRespirationValue": 14.2,
                "avgSleepRespirationValue": 12.8,
                "lowestRespirationValue": 10.1,
                "highestRespirationValue": highest_respiration,
            },
            hrv={
                "hrvSummary": {"lastNightAvg": nightly_hrv},
                "hrvReadings": [
                    {"readingTimeGMT": "2026-01-10T08:00:00Z", "hrvValue": 40},
                    {"readingTimeGMT": "2026-01-10T08:05:00Z", "hrvValue": 42},
                ],
            },
            steps=[],
            timezone="UTC",
        )
        return FetchedWindow(
            start_date=date(2026, 1, 10),
            end_date=date(2026, 1, 10),
            timezone="UTC",
            metrics=(*daily.metrics, *intraday.aggregates),
            intraday_metrics=intraday.observations,
            sleeps=(daily.sleep,),
            activities=activities,
            warnings=tuple(sorted({*daily.warnings, *intraday.warnings, *activity_warnings})),
            capabilities={
                **daily.capabilities,
                **intraday.capabilities,
                "activities": "available",
            },
            started_at=observed_at,
            finished_at=observed_at + timedelta(seconds=1),
        )

    with Session(engine) as session, session.begin():
        first = persist_window(session, owner_id=owner_id, fetched=fetched(100, 72))
        assert (first.created, first.corrected, first.unchanged) == (14, 0, 0)
    with Session(engine) as session, session.begin():
        duplicate = persist_window(session, owner_id=owner_id, fetched=fetched(100, 72))
        assert (duplicate.created, duplicate.corrected, duplicate.unchanged) == (0, 0, 14)
    with Session(engine) as session, session.begin():
        corrected = persist_window(
            session, owner_id=owner_id, fetched=fetched(120, 73, 43, 19.2, 12)
        )
        assert (corrected.created, corrected.corrected, corrected.unchanged) == (0, 5, 9)

    with Session(engine) as session:
        metrics = list(
            session.scalars(select(GarminMetricEvent).where(GarminMetricEvent.owner_id == owner_id))
        )
        current = events.current_only(session, GarminMetricEvent, metrics)
        assert len(metrics) == 16
        assert len(current) == 12
        daily_current = [row for row in current if row.aggregation == "daily_summary"]
        assert len(daily_current) == 6
        steps = next(row for row in daily_current if row.metric_type is GarminMetricType.STEPS)
        assert steps.value == Decimal(120)
        assert steps.supersedes_id is not None
        assert steps.garmin_sync_run_id is not None
        assert {
            row.garmin_field_name
            for row in daily_current
            if row.metric_type is GarminMetricType.HRV
        } == {"lastNightAvg"}
        nightly_hrv_row = next(
            row for row in daily_current if row.garmin_field_name == "lastNightAvg"
        )
        assert nightly_hrv_row.value == Decimal(43)
        assert nightly_hrv_row.supersedes_id is not None
        assert {
            row.garmin_field_name
            for row in daily_current
            if row.metric_type is GarminMetricType.RESPIRATION_RATE
        } == {
            "avgWakingRespirationValue",
            "avgSleepRespirationValue",
            "lowestRespirationValue",
            "highestRespirationValue",
        }
        samples = [row for row in current if row.aggregation == "provider_sample"]
        assert len(samples) == 6
        assert any(row.value == 0 and row.metric_type.value == "stress" for row in samples)
        assert any(row.sample_interval_seconds == 120 for row in samples)
        assert any(row.sample_interval_seconds == 300 for row in samples)
        activity = session.scalar(
            select(GarminActivityEvent).where(GarminActivityEvent.owner_id == owner_id)
        )
        assert activity is not None
        assert activity.distance_miles == Decimal(1)
        sleeps = list(
            session.scalars(select(GarminSleepEvent).where(GarminSleepEvent.owner_id == owner_id))
        )
        assert len(sleeps) == 2
        assert len(events.current_only(session, GarminSleepEvent, sleeps)) == 1
        assert (
            session.scalar(
                select(func.count())
                .select_from(GarminSleepStageInterval)
                .join(
                    GarminSleepEvent,
                    GarminSleepEvent.id == GarminSleepStageInterval.sleep_event_id,
                )
                .where(GarminSleepEvent.owner_id == owner_id)
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(GarminSyncRun)
                .where(GarminSyncRun.owner_id == owner_id)
            )
            == 3
        )

    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200
    headers = {auth.CSRF_HEADER_NAME: login.json()["csrf_token"]}
    records = client.get("/api/v1/integrations/garmin/records")
    assert records.status_code == 200
    assert {row["kind"] for row in records.json()["records"]} == {
        "daily",
        "sleep",
        "activity",
    }
    assert any(row["distance_miles"] == "1.0000" for row in records.json()["records"])
    nightly_hrv = next(
        row for row in records.json()["records"] if row["garmin_field_name"] == "lastNightAvg"
    )
    assert nightly_hrv["measurement_label"] == "Nightly average HRV"
    assert nightly_hrv["period_label"] == "previous night"
    assert nightly_hrv["value"] == "43.0000"
    assert nightly_hrv["unit"] == "ms"
    waking_respiration = next(
        row
        for row in records.json()["records"]
        if row["garmin_field_name"] == "avgWakingRespirationValue"
    )
    assert waking_respiration["measurement_label"] == "Average waking respiration"
    assert waking_respiration["period_label"] == "waking period"
    secondary_record_ids = {row["id"] for row in records.json()["records"]}
    sample_response = client.get(
        "/api/v1/integrations/garmin/samples",
        params={"day": "2026-01-10", "timezone": "UTC", "page_size": 100},
    )
    assert sample_response.status_code == 200, sample_response.text
    sample_body = sample_response.json()
    assert sample_body["page"]["total_items"] == 6
    assert {row["kind"] for row in sample_body["records"]} == {"sample"}
    assert {row["aggregation"] for row in sample_body["records"]} == {"provider_sample"}
    assert any(row["value"] == "0.0000" for row in sample_body["records"])
    assert (
        client.get(
            "/api/v1/integrations/garmin/samples",
            params={"day": "2026-01-11", "timezone": "UTC"},
        ).json()["records"]
        == []
    )
    sleep_response = client.get(
        "/api/v1/integrations/garmin/sleep",
        params={"day": "2026-01-10", "timezone": "UTC", "page_size": 100},
    )
    assert sleep_response.status_code == 200, sleep_response.text
    sleep_body = sleep_response.json()
    assert sleep_body["page"]["total_items"] == 1
    assert sleep_body["records"][0]["time"]["occurred_at"] == "2026-01-09T23:00:00Z"
    assert sleep_body["records"][0]["ended_at"] == "2026-01-10T07:00:00Z"
    assert sleep_body["records"][0]["sleep_intervals"] == [
        {
            "stage": "awake",
            "started_at": "2026-01-10T02:00:00Z",
            "ended_at": "2026-01-10T02:12:00Z",
        }
    ]
    status_response = client.get("/api/v1/integrations/garmin/status")
    assert status_response.status_code == 200
    assert status_response.json()["state"] == "connected"
    assert status_response.json()["last_success_at"] is not None
    assert status_response.json()["capabilities"]["hrv_daily_average"] == "unsupported"
    assert status_response.json()["capabilities"]["hrv_nightly_average"] == "available"
    assert status_response.json()["latest_sync_origin"] == "legacy"
    garmin_timeline = client.get(
        "/api/v1/timeline?types=garmin_daily,garmin_activity&sort_order=asc"
    )
    assert garmin_timeline.status_code == 200
    assert [item["event_type"] for item in garmin_timeline.json()["items"]].count(
        "garmin_daily"
    ) == 6
    assert [item["event_type"] for item in garmin_timeline.json()["items"]].count(
        "garmin_activity"
    ) == 1
    assert {item["summary"] for item in garmin_timeline.json()["items"]} == {
        "Steps: 120.0000 steps",
        "Nightly average HRV: 43.0000 ms",
        "Average waking respiration: 14.2000 breaths/min",
        "Average sleeping respiration: 12.8000 breaths/min",
        "Lowest respiration: 10.1000 breaths/min",
        "Highest respiration: 19.2000 breaths/min",
        "Activity: Walking; 1.0000 mi",
    }
    assert all(
        item["provenance"]["source_type"] == "provider" for item in garmin_timeline.json()["items"]
    )
    assert all("Garmin-recorded" not in item["summary"] for item in garmin_timeline.json()["items"])
    quality = client.get("/api/v1/data-quality")
    assert quality.status_code == 200
    assert any(
        finding["finding_kind"] == "genuine_absence" and "no zero" in finding["detail"]
        for finding in quality.json()["findings"]
    )
    _, _, private_export = _complete_private_export(
        client, headers, engine, settings, key="garmin-owner-export"
    )
    assert private_export.status_code == 200
    garmin_export = private_export.json()["integrations"]
    assert len(garmin_export["garmin_connection_state"]) == 1
    assert len(garmin_export["garmin_sync_runs"]) == 3
    assert len(private_export.json()["facts"]["garmin_metrics"]) == 16
    assert len(private_export.json()["facts"]["garmin_sleep_stage_intervals"]) == 2
    assert any(
        row["garmin_field_name"] == "lastNightAvg" and row["aggregation"] == "daily_summary"
        for row in private_export.json()["facts"]["garmin_metrics"]
    )
    assert "credentials" not in garmin_export

    disabled_sync = client.post(
        "/api/v1/integrations/garmin/sync",
        headers={**headers, "Idempotency-Key": "synthetic-disabled"},
    )
    assert disabled_sync.status_code == 409
    settings.garmin_enabled = True
    try:
        future_sync = client.post(
            "/api/v1/integrations/garmin/sync?date_from=2030-01-01&date_to=2030-01-02",
            headers={**headers, "Idempotency-Key": "synthetic-future"},
        )
        assert future_sync.status_code == 422
        requested_sync = client.post(
            "/api/v1/integrations/garmin/sync?date_from=2026-01-10&date_to=2026-01-11",
            headers={**headers, "Idempotency-Key": "synthetic-manual-sync"},
        )
        duplicate_sync = client.post(
            "/api/v1/integrations/garmin/sync?date_from=2026-01-10&date_to=2026-01-11",
            headers={**headers, "Idempotency-Key": "synthetic-manual-sync"},
        )
        equivalent_sync = client.post(
            "/api/v1/integrations/garmin/sync?date_from=2026-01-10&date_to=2026-01-11",
            headers={**headers, "Idempotency-Key": "synthetic-manual-sync-new-key"},
        )
        assert requested_sync.status_code == 202
        assert duplicate_sync.status_code == 202
        assert equivalent_sync.status_code == 202
        assert requested_sync.json()["job_id"] == duplicate_sync.json()["job_id"]
        assert requested_sync.json()["job_id"] == equivalent_sync.json()["job_id"]
        assert requested_sync.json()["disposition"] == "queued"
        assert requested_sync.json()["origin"] == "manual"
        assert duplicate_sync.json()["disposition"] == "coalesced_active"
        assert equivalent_sync.json()["disposition"] == "coalesced_active"
    finally:
        settings.garmin_enabled = False

    invalid = client.request(
        "DELETE",
        "/api/v1/privacy/integrations/garmin",
        headers=headers,
        json={
            "password": PASSWORD,
            "delete_data": True,
            "confirmation": "DISCONNECT GARMIN",
        },
    )
    assert invalid.status_code == 422
    deleted = client.request(
        "DELETE",
        "/api/v1/privacy/integrations/garmin",
        headers=headers,
        json={
            "password": PASSWORD,
            "delete_data": True,
            "confirmation": "DISCONNECT GARMIN AND DELETE DATA",
        },
    )
    assert deleted.status_code == 200, deleted.text
    assert deleted.json()["disconnect_requested"] is True
    assert deleted.json()["data_rows_deleted"] == 24

    class LogoutClient:
        logged_out = False

        def login(self) -> None:
            raise AssertionError("disconnect must not log in")

        def get_stats(self, day: str) -> dict[str, Any]:
            raise AssertionError("disconnect must not read data")

        def get_sleep_data(self, day: str) -> dict[str, Any]:
            raise AssertionError("disconnect must not read data")

        def get_heart_rates(self, day: str) -> dict[str, Any]:
            raise AssertionError("disconnect must not read data")

        def get_stress_data(self, day: str) -> dict[str, Any]:
            raise AssertionError("disconnect must not read data")

        def get_respiration_data(self, day: str) -> dict[str, Any]:
            raise AssertionError("disconnect must not read data")

        def get_hrv_data(self, day: str) -> dict[str, Any]:
            raise AssertionError("disconnect must not read data")

        def get_steps_data(self, day: str) -> list[dict[str, Any]]:
            raise AssertionError("disconnect must not read data")

        def get_activities_by_date(self, start: str, end: str) -> list[dict[str, Any]]:
            raise AssertionError("disconnect must not read data")

        def logout(self) -> None:
            self.logged_out = True

    logout_client = LogoutClient()
    handler = make_disconnect_handler(settings, client_factory=lambda: logout_client)
    with Session(engine) as session, session.begin():
        job = session.scalar(
            select(Job).where(
                Job.task == GARMIN_DISCONNECT_TASK,
                Job.payload["owner_id"].as_string() == str(owner_id),
            )
        )
        assert job is not None
        handler(session, job.payload)
    assert logout_client.logged_out

    with Session(engine) as session:
        connection = session.scalar(
            select(GarminConnection).where(GarminConnection.owner_id == owner_id)
        )
        assert connection is not None
        assert connection.state is GarminConnectionState.DISCONNECTED
        assert (
            session.scalar(
                select(func.count())
                .select_from(GarminMetricEvent)
                .where(GarminMetricEvent.owner_id == owner_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(GarminActivityEvent)
                .where(GarminActivityEvent.owner_id == owner_id)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(GarminSyncRun)
                .where(GarminSyncRun.owner_id == owner_id)
            )
            == 0
        )

    primary_login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert primary_login.status_code == 200
    primary_records = client.get("/api/v1/integrations/garmin/records")
    assert primary_records.status_code == 200
    assert secondary_record_ids.isdisjoint(row["id"] for row in primary_records.json()["records"])
    primary_sleep = client.get(
        "/api/v1/integrations/garmin/sleep",
        params={"day": "2026-01-10", "timezone": "UTC"},
    )
    assert primary_sleep.status_code == 200
    assert primary_sleep.json()["records"] == []


def test_lab_csv_preview_flags_unknowns_then_confirm_is_idempotent(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    csv_payload = (
        b"Analyte,Value,Qualitative,Unit,Range,Flag\n"
        b"Known synthetic analyte,12.3,,nmol/L,5-10,H\n"
        b"Mystery synthetic analyte,,Not detected,,,\n"
        b"SYNTHETIC TEST,7,,units,1-9,\n"
    )
    upload = {"file": ("synthetic-labs.csv", csv_payload, "text/csv")}
    data = {
        "mapping_json": (
            '{"analyte":"Analyte","value":"Value","qualitative":"Qualitative",'
            '"unit":"Unit","reference_range":"Range","abnormal_flag":"Flag"}'
        ),
        "analyte_map_json": (
            '{"Known synthetic analyte":"known-code",'
            '"Synthetic Test":"first-code","synthetic test":"second-code"}'
        ),
        "specimen_local": "2026-08-09T07:30:00",
        "report_local": "2026-08-09T09:00:00",
        "timezone": "Europe/London",
    }
    with Session(engine) as session:
        before_panels = session.scalar(select(func.count()).select_from(LabPanel))
        before_results = session.scalar(select(func.count()).select_from(LabResult))
        assert before_panels is not None
        assert before_results is not None

    preview = client.post(
        "/api/v1/labs/imports/csv/preview",
        files=upload,
        data=data,
        headers=logged_in,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["creates_facts"] is False
    assert body["candidates"][0]["normalized_analyte_code"] == "known-code"
    assert body["candidates"][1]["flags"] == ["unrecognized_analyte"]
    assert body["candidates"][2]["flags"] == ["ambiguous_analyte"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabPanel)) == before_panels
        assert session.scalar(select(func.count()).select_from(LabResult)) == before_results

    mismatch = client.post(
        "/api/v1/labs/imports/csv/confirm",
        files=upload,
        data={**data, "expected_sha256": "0" * 64},
        headers=logged_in,
    )
    assert mismatch.status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabPanel)) == before_panels

    confirm_data = {**data, "expected_sha256": body["source_sha256"]}
    first = client.post(
        "/api/v1/labs/imports/csv/confirm",
        files=upload,
        data=confirm_data,
        headers=logged_in,
    )
    second = client.post(
        "/api/v1/labs/imports/csv/confirm",
        files=upload,
        data=confirm_data,
        headers=logged_in,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["panel_id"] == first.json()["panel_id"]
    assert first.json()["result_count"] == 3
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabPanel)) == before_panels + 1
        assert session.scalar(select(func.count()).select_from(LabResult)) == before_results + 3
        imported = session.scalar(
            select(LabPanel).where(LabPanel.id == uuid.UUID(first.json()["panel_id"]))
        )
        assert imported is not None
        assert imported.confirmation_state.value == "confirmed_from_draft"
        assert imported.results[1].qualitative_result == "Not detected"
        assert imported.results[1].normalized_analyte_code is None


def test_manual_qualitative_lab_entry_is_a_direct_fact(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    response = client.post(
        "/api/v1/labs/manual",
        headers=logged_in,
        json={
            "specimen_time": {
                "local_time": "2026-08-08T10:15:00",
                "timezone": "Europe/London",
            },
            "report_time": {
                "local_time": "2026-08-08T12:30:00",
                "timezone": "Europe/London",
            },
            "laboratory_name": "Synthetic laboratory",
            "results": [
                {
                    "analyte_name": "Synthetic qualitative result",
                    "qualitative_result": "Not detected (verbatim)",
                    "abnormal_flag": "Lab supplied flag",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["category"] == "fact"
    with Session(engine) as session:
        panel = session.get(LabPanel, uuid.UUID(response.json()["panel_id"]))
        assert panel is not None
        assert panel.source_type.value == "web"
        assert panel.confirmation_state.value == "direct"
        assert panel.results[0].qualitative_result == "Not detected (verbatim)"
        assert panel.results[0].abnormal_flag == "Lab supplied flag"


def test_curated_lab_normalization_preserves_source_and_cortisol_context(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    created = client.post(
        "/api/v1/labs/manual",
        headers=logged_in,
        json={
            "specimen_time": {
                "local_time": "2026-08-08T07:45:00",
                "timezone": "America/New_York",
            },
            "report_time": {
                "local_time": "2026-08-08T09:30:00",
                "timezone": "America/New_York",
            },
            "laboratory_name": "Synthetic laboratory",
            "specimen_type": "Serum",
            "results": [
                {
                    "analyte_name": "Cortisol AM",
                    "original_value": "10",
                    "original_unit": "mcg/dL",
                    "original_reference_range": "6-18 source range",
                },
                {
                    "analyte_name": "Synthetic out-of-scope marker",
                    "original_value": "42",
                    "original_unit": "widgets",
                },
            ],
        },
    )
    assert created.status_code == 201, created.text
    panel_id = uuid.UUID(created.json()["panel_id"])

    listed = client.get("/api/v1/labs/results")
    assert listed.status_code == 200, listed.text
    rows = [row for row in listed.json()["items"] if row["panel_id"] == str(panel_id)]
    assert len(rows) == 2
    cortisol = rows[0]
    assert cortisol["category"] == "fact"
    assert cortisol["analyte_name"] == "Cortisol AM"
    assert cortisol["original_value"] == "10"
    assert cortisol["original_unit"] == "mcg/dL"
    assert cortisol["original_reference_range"] == "6-18 source range"
    assert cortisol["normalized_analyte_code"] == "cortisol"
    assert cortisol["normalized_value"] == "276.0000000000"
    assert cortisol["normalized_unit"] == "nmol/L"
    assert cortisol["normalization_method"].startswith("hc-lab-normalization-v1")
    assert cortisol["specimen_type"] == "Serum"
    assert cortisol["specimen_time"]["local_time"] == "2026-08-08T07:45:00"
    assert cortisol["specimen_time"]["timezone"] == "America/New_York"
    assert rows[1]["normalized_analyte_code"] is None
    assert rows[1]["normalized_value"] is None

    with Session(engine) as session:
        panel = session.get(LabPanel, panel_id)
        assert panel is not None
        assert panel.results[0].original_value == "10"
        assert panel.results[0].normalized_value == Decimal("276.0000000000")
        panel.results[0].normalized_analyte_code = None
        panel.results[0].normalized_value = None
        panel.results[0].normalized_unit = None
        panel.results[0].normalization_method = None
        session.commit()

    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        assert backfill_normalizations(session, owner_id=owner_id) == 1
    with Session(engine) as session:
        panel = session.get(LabPanel, panel_id)
        assert panel is not None
        assert panel.results[0].original_value == "10"
        assert panel.results[0].original_unit == "mcg/dL"
        assert panel.results[0].normalized_value == Decimal("276.0000000000")
        entries = session.scalars(
            select(AuditEntry).where(AuditEntry.target_type == "lab_normalization_derivation")
        ).all()
        assert entries[-1].change_summary == (
            "derived_fields_recomputed;version=hc-lab-normalization-v1;count=1"
        )


def test_pdf_upload_is_quarantined_validated_downloaded_as_attachment_and_deleted(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    source = b"%PDF-1.7\nsynthetic fixture only\n"
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("../Synthetic report.pdf", source, "application/pdf")},
    )
    assert uploaded.status_code == 202, uploaded.text
    body = uploaded.json()
    assert body["status"] == "pending"
    assert body["display_name"] == "Synthetic report.pdf"
    document_id = uuid.UUID(body["document_id"])
    layout = DocumentLayout(settings.uploads_dir)
    assert layout.path("quarantine", document_id).read_bytes() == source

    validation = validate_one(layout, document_id, runner=QpdfRunner(pages=2))
    assert validation.status == "stored"
    status_response = client.get(f"/api/v1/labs/documents/{document_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "stored"
    assert status_response.json()["page_count"] == 2

    downloaded = client.get(f"/api/v1/labs/documents/{document_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == source
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "no-store"

    preview = client.get(f"/api/v1/labs/documents/{document_id}/deletion-preview")
    assert preview.status_code == 200, preview.text
    impact = preview.json()
    assert impact["mode"] == "unconfirmed_upload"
    assert impact["requires_password"] is False
    assert impact["panel_ids"] == []
    assert impact["result_ids"] == []
    assert impact["report_snapshot_ids"] == []
    assert impact["private_storage_artifact_count"] >= 1

    mismatch = client.request(
        "DELETE",
        f"/api/v1/labs/documents/{document_id}",
        headers=logged_in,
        json={"password": None, "confirmation": "DELETE THE WRONG TARGET"},
    )
    assert mismatch.status_code == 422
    deleted = client.request(
        "DELETE",
        f"/api/v1/labs/documents/{document_id}",
        headers=logged_in,
        json={"password": None, "confirmation": impact["confirmation_phrase"]},
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["cleanup_task_count"] == 1
    # Database privacy deletion commits with the durable job; physical bytes remain
    # unavailable to the API and are removed by the retryable worker.
    assert layout.path("stored", document_id).exists()
    with Session(engine) as session:
        document = session.get(LabDocument, document_id)
        assert document is not None
        assert document.status is LabDocumentStatus.DELETED
        assert document.display_name == "deleted.pdf"
        assert document.sha256 == "0" * 64
        job = session.scalar(
            select(Job).where(
                Job.task == LAB_DOCUMENT_CLEANUP_TASK,
                Job.idempotency_key == f"lab-document:{document_id}",
            )
        )
        assert job is not None and job.status is JobStatus.QUEUED

    factory = sessionmaker(engine, expire_on_commit=False)
    claimed = queue_worker.run_once(
        factory,
        {LAB_DOCUMENT_CLEANUP_TASK: make_document_cleanup_handler(layout)},
        worker_id="synthetic-lab-cleanup",
    )
    assert claimed is not None
    assert not layout.path("stored", document_id).exists()
    assert layout.path("tombstones", document_id, ".deleted").exists()
    repeated = client.request(
        "DELETE",
        f"/api/v1/labs/documents/{document_id}",
        headers=logged_in,
        json={"password": None, "confirmation": impact["confirmation_phrase"]},
    )
    assert repeated.status_code == 202
    assert repeated.json()["status"] == "already_deleted"


def test_pdf_upload_rejects_spoofed_content_type_and_signature(
    client: TestClient, logged_in: dict[str, str], settings: Settings
) -> None:
    wrong_type = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic.pdf", b"%PDF-1.7\n", "text/plain")},
    )
    wrong_signature = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic.pdf", b"not a PDF", "application/pdf")},
    )
    assert wrong_type.status_code == 415
    assert wrong_type.json()["detail"]["code"] == "pdf_media_type_invalid"
    assert wrong_signature.status_code == 422
    assert wrong_signature.json()["detail"]["code"] == "pdf_signature_invalid"

    structurally_bad = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic.pdf", b"%PDF-1.7\nmalformed", "application/pdf")},
    )
    assert structurally_bad.status_code == 202
    document_id = uuid.UUID(structurally_bad.json()["document_id"])
    result = validate_one(
        DocumentLayout(settings.uploads_dir), document_id, runner=QpdfRunner(check_code=2)
    )
    assert result.reason_code == "pdf_structure_invalid"
    rejected = client.get(f"/api/v1/labs/documents/{document_id}")
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejection_reason"] == "pdf_structure_invalid"


def test_lab_deletion_queue_failure_rolls_back_database_state(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic-rollback.pdf", b"%PDF-1.7\nsynthetic\n", "application/pdf")},
    )
    document_id = uuid.UUID(uploaded.json()["document_id"])
    preview = client.get(f"/api/v1/labs/documents/{document_id}/deletion-preview").json()

    with mock.patch(
        "healthcurve.api.lab_deletion.enqueue_document_cleanup",
        side_effect=JobQueueError("synthetic_queue_unavailable"),
    ):
        failed = client.request(
            "DELETE",
            f"/api/v1/labs/documents/{document_id}",
            headers=logged_in,
            json={"password": None, "confirmation": preview["confirmation_phrase"]},
        )
    assert failed.status_code == 503
    with Session(engine) as session:
        document = session.get(LabDocument, document_id)
        assert document is not None
        assert document.status is LabDocumentStatus.PENDING
        assert (
            session.scalar(
                select(Job.id).where(
                    Job.task == LAB_DOCUMENT_CLEANUP_TASK,
                    Job.idempotency_key == f"lab-document:{document_id}",
                )
            )
            is None
        )
    assert DocumentLayout(settings.uploads_dir).path("quarantine", document_id).is_file()


def test_lab_deletion_preview_and_delete_hide_another_owners_document(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic-owned.pdf", b"%PDF-1.7\nsynthetic\n", "application/pdf")},
    )
    document_id = uuid.UUID(uploaded.json()["document_id"])
    other_email = f"lab-delete-other-{uuid.uuid4()}@example.com"
    with Session(engine) as session, session.begin():
        session.add(
            Owner(
                email=other_email,
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="UTC",
            )
        )
    other_login = client.post(
        "/api/v1/auth/login", json={"email": other_email, "password": PASSWORD}
    )
    assert other_login.status_code == 200, other_login.text
    other_headers = {auth.CSRF_HEADER_NAME: other_login.json()["csrf_token"]}

    assert client.get(f"/api/v1/labs/documents/{document_id}/deletion-preview").status_code == 404
    hidden_delete = client.request(
        "DELETE",
        f"/api/v1/labs/documents/{document_id}",
        headers=other_headers,
        json={"password": PASSWORD, "confirmation": "DELETE LAB UPLOAD 00000000"},
    )
    assert hidden_delete.status_code == 404

    owner_login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    owner_headers = {auth.CSRF_HEADER_NAME: owner_login.json()["csrf_token"]}
    preview = client.get(f"/api/v1/labs/documents/{document_id}/deletion-preview").json()
    deleted = client.request(
        "DELETE",
        f"/api/v1/labs/documents/{document_id}",
        headers=owner_headers,
        json={"password": None, "confirmation": preview["confirmation_phrase"]},
    )
    assert deleted.status_code == 202
    factory = sessionmaker(engine, expire_on_commit=False)
    assert (
        queue_worker.run_once(
            factory,
            {
                LAB_DOCUMENT_CLEANUP_TASK: make_document_cleanup_handler(
                    DocumentLayout(settings.uploads_dir)
                )
            },
            worker_id="synthetic-owner-scope-cleanup",
        )
        is not None
    )


def test_digital_pdf_extraction_creates_only_review_draft_and_keeps_unparsed_rows(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    payload = synthetic_text_lab_pdf()
    with Session(engine) as session:
        facts_before = session.scalar(select(func.count()).select_from(LabResult))
        drafts_before = session.scalar(select(func.count()).select_from(ExtractionDraft))
        assert facts_before is not None
        assert drafts_before is not None
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic-digital.pdf", payload, "application/pdf")},
    )
    assert uploaded.status_code == 202
    document_id = uuid.UUID(uploaded.json()["document_id"])
    process_available(DocumentLayout(settings.uploads_dir), runner=QpdfRunner())

    extraction = client.get(f"/api/v1/labs/documents/{document_id}/extraction")
    assert extraction.status_code == 200, extraction.text
    body = extraction.json()
    assert body["category"] == "ai_draft"
    assert body["model_name"] is None
    parsed = [candidate for candidate in body["candidates"] if candidate["parsed"]]
    unparsed = [candidate for candidate in body["candidates"] if not candidate["parsed"]]
    assert parsed[0]["extraction_tier"] == "embedded_text"
    assert parsed[0]["extractor_name"] == "pdfplumber"
    assert parsed[0]["requires_confirmation"] is True
    assert parsed[0]["analyte_name"] == "Synthetic sodium"
    assert {candidate["source_text"] for candidate in unparsed} == {
        "Synthetic laboratory panel",
        "Synthetic unparsed note",
    }
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabResult)) == facts_before
        assert (
            session.scalar(select(func.count()).select_from(ExtractionDraft)) == drafts_before + 1
        )


def test_pdf_review_correction_confirmation_and_source_page_link_are_idempotent(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={
            "file": (
                "synthetic-review.pdf",
                synthetic_text_lab_pdf(),
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 202, uploaded.text
    document_id = uuid.UUID(uploaded.json()["document_id"])
    process_available(DocumentLayout(settings.uploads_dir), runner=QpdfRunner())
    extraction = client.get(f"/api/v1/labs/documents/{document_id}/extraction")
    assert extraction.status_code == 200, extraction.text
    parsed = [
        (index, candidate)
        for index, candidate in enumerate(extraction.json()["candidates"])
        if candidate["parsed"]
    ]
    assert len(parsed) == 1
    candidate_index, candidate = parsed[0]

    before = client.get("/api/v1/labs/results").json()["items"]
    assert not any(row["source_document_id"] == str(document_id) for row in before)
    confirmation = {
        "specimen_time": {
            "local_time": "2026-08-09T08:00:00",
            "timezone": "Europe/London",
        },
        "report_time": {
            "local_time": "2026-08-09T09:00:00",
            "timezone": "Europe/London",
        },
        "laboratory_name": "Synthetic reviewed laboratory",
        "specimen_type": "Synthetic serum",
        "candidates": [
            {
                "candidate_index": candidate_index,
                "included": True,
                "analyte_name": "Synthetic sodium corrected",
                "original_value": candidate["original_value"],
                "original_unit": candidate["original_unit"],
                "original_reference_range": candidate["original_reference_range"],
            }
        ],
    }
    layout = DocumentLayout(settings.uploads_dir)
    layout.preview_path(document_id, 1).unlink()
    missing_preview = client.post(
        f"/api/v1/labs/documents/{document_id}/confirm",
        headers=logged_in,
        json=confirmation,
    )
    assert missing_preview.status_code == 409
    assert missing_preview.json()["detail"]["code"] == "lab_source_preview_unavailable"
    assert not any(
        row["source_document_id"] == str(document_id)
        for row in client.get("/api/v1/labs/results").json()["items"]
    )
    process_available(layout, runner=QpdfRunner())
    assert layout.preview_path(document_id, 1).is_file()
    confirmed = client.post(
        f"/api/v1/labs/documents/{document_id}/confirm",
        headers=logged_in,
        json=confirmation,
    )
    assert confirmed.status_code == 200, confirmed.text
    assert confirmed.json()["created"] is True
    repeated = client.post(
        f"/api/v1/labs/documents/{document_id}/confirm",
        headers=logged_in,
        json=confirmation,
    )
    assert repeated.status_code == 200, repeated.text
    assert repeated.json()["created"] is False
    assert repeated.json()["panel_id"] == confirmed.json()["panel_id"]

    rows = [
        row
        for row in client.get("/api/v1/labs/results").json()["items"]
        if row["source_document_id"] == str(document_id)
    ]
    assert len(rows) == 1
    assert rows[0]["analyte_name"] == "Synthetic sodium corrected"
    assert rows[0]["source_page_number"] == 1
    assert rows[0]["source_type"] == "file_import"
    assert rows[0]["confirmation_state"] == "confirmed_from_draft"
    previewed = client.get(f"/api/v1/labs/documents/{document_id}/pages/1/preview")
    assert previewed.status_code == 200
    assert previewed.headers["content-type"] == "image/png"
    assert previewed.headers["content-disposition"].startswith("inline;")
    assert previewed.headers["x-content-type-options"] == "nosniff"
    assert previewed.headers["cache-control"] == "no-store"
    assert previewed.content.startswith(b"\x89PNG\r\n\x1a\n")
    assert client.get(f"/api/v1/labs/documents/{document_id}/view").status_code == 404
    result_id = uuid.UUID(rows[0]["id"])
    panel_id = uuid.UUID(confirmed.json()["panel_id"])
    with Session(engine) as session:
        draft = session.scalar(
            select(ExtractionDraft).where(ExtractionDraft.provider_message_id == str(document_id))
        )
        assert draft is not None
        assert draft.state is DraftState.EDITED
        assert draft.created_event_ids == [confirmed.json()["panel_id"]]
        draft_id = draft.id
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        analysis = AIAnalysis(
            owner_id=owner_id,
            analysis_type=AnalysisType.PATTERN_OBSERVATION,
            body="Synthetic analysis for deletion dependency coverage.",
            source_record_ids=[str(result_id)],
            computed_inputs={"synthetic": True},
            model_name="synthetic-model",
            model_digest="a" * 64,
            prompt_version="synthetic-v1",
        )
        session.add(analysis)
        unrelated_analysis = AIAnalysis(
            owner_id=owner_id,
            analysis_type=AnalysisType.PATTERN_OBSERVATION,
            body="Synthetic unrelated analysis retained by deletion.",
            source_record_ids=[str(uuid.uuid4())],
            range_start=datetime(2035, 1, 1, tzinfo=UTC),
            range_end=datetime(2035, 1, 2, tzinfo=UTC),
            computed_inputs={"synthetic": True},
            model_name="synthetic-model",
            model_digest="a" * 64,
            prompt_version="synthetic-v1",
        )
        unrelated_snapshot = ReportSnapshot(
            owner_id=owner_id,
            date_from=date(2026, 8, 8),
            date_to=date(2026, 8, 8),
            timezone="UTC",
            selected_sections=["metrics"],
            include_ai=False,
            source_manifest={"fact": [], "plan": [], "patient_note": [], "ai": []},
            metric_values={},
            snapshot_content={"fact": [], "plan": [], "patient_note": [], "ai": []},
            render_version="synthetic-unrelated-v1",
            canonical_sha256="f" * 64,
        )
        session.add_all([unrelated_analysis, unrelated_snapshot])
        session.commit()
        analysis_id = analysis.id
        unrelated_analysis_id = unrelated_analysis.id
        unrelated_snapshot_id = unrelated_snapshot.id

    report = client.post(
        "/api/v1/reports",
        headers=logged_in,
        json={
            "date_from": "2026-08-09",
            "date_to": "2026-08-09",
            "timezone": "Europe/London",
            "selected_sections": ["labs"],
            "include_ai": False,
            "include_sensitive": False,
            "companion_formats": ["json"],
        },
    )
    assert report.status_code == 201, report.text
    report_id = uuid.UUID(report.json()["id"])
    report_artifact_ids: set[str]
    with Session(engine) as session:
        report_artifact_ids = {
            str(value)
            for value in session.scalars(
                select(ReportArtifact.id).where(ReportArtifact.snapshot_id == report_id)
            )
        }
    assert len(report_artifact_ids) == 2

    deletion_preview = client.get(f"/api/v1/labs/documents/{document_id}/deletion-preview")
    assert deletion_preview.status_code == 200, deletion_preview.text
    impact = deletion_preview.json()
    assert impact["mode"] == "confirmed_report"
    assert impact["requires_password"] is True
    assert impact["panel_ids"] == [str(panel_id)]
    assert impact["result_ids"] == [str(result_id)]
    assert impact["derived_result_count"] == 0
    assert impact["trend_point_count"] == 0
    assert impact["ai_analysis_ids"] == [str(analysis_id)]
    assert impact["report_snapshot_ids"] == [str(report_id)]
    assert set(impact["report_artifact_ids"]) == report_artifact_ids
    assert impact["page_preview_count"] == 1

    wrong_phrase = client.request(
        "DELETE",
        f"/api/v1/labs/documents/{document_id}",
        headers=logged_in,
        json={"password": PASSWORD, "confirmation": "DELETE A DIFFERENT REPORT"},
    )
    assert wrong_phrase.status_code == 422
    wrong_password = client.request(
        "DELETE",
        f"/api/v1/labs/documents/{document_id}",
        headers=logged_in,
        json={"password": PASSWORD + "-wrong", "confirmation": impact["confirmation_phrase"]},
    )
    assert wrong_password.status_code == 403
    assert any(
        row["source_document_id"] == str(document_id)
        for row in client.get("/api/v1/labs/results").json()["items"]
    )

    deleted = client.request(
        "DELETE",
        f"/api/v1/labs/documents/{document_id}",
        headers=logged_in,
        json={"password": PASSWORD, "confirmation": impact["confirmation_phrase"]},
    )
    assert deleted.status_code == 202, deleted.text
    assert deleted.json()["cleanup_task_count"] == 2
    assert not any(
        row["source_document_id"] == str(document_id)
        for row in client.get("/api/v1/labs/results").json()["items"]
    )
    assert client.get(f"/api/v1/reports/{report_id}").status_code == 404
    assert client.get(f"/api/v1/labs/documents/{document_id}/download").status_code == 409
    with Session(engine) as session:
        assert session.get(LabPanel, panel_id) is None
        assert session.get(LabResult, result_id) is None
        assert session.get(ExtractionDraft, draft_id) is None
        assert session.get(AIAnalysis, analysis_id) is None
        assert session.get(ReportSnapshot, report_id) is None
        assert session.get(AIAnalysis, unrelated_analysis_id) is not None
        assert session.get(ReportSnapshot, unrelated_snapshot_id) is not None
        entry = session.scalar(
            select(AuditEntry)
            .where(
                AuditEntry.target_type == "lab_report_unit",
                AuditEntry.target_id == document_id,
            )
            .order_by(AuditEntry.occurred_at.desc())
        )
        assert entry is not None
        assert entry.change_summary == (
            "mode=confirmed_report;drafts=1;panels=1;results=1;analyses=1;reports=1;cleanup_jobs=2"
        )
        assert "Synthetic" not in entry.change_summary

    factory = sessionmaker(engine, expire_on_commit=False)
    handlers = {
        LAB_DOCUMENT_CLEANUP_TASK: make_document_cleanup_handler(layout),
        REPORT_ARTIFACT_CLEANUP_TASK: make_snapshot_artifact_cleanup_handler(
            settings.report_artifacts_dir
        ),
    }
    claimed_tasks: set[str] = set()
    for _ in range(3):
        claimed = queue_worker.run_once(factory, handlers, worker_id="synthetic-confirmed-cleanup")
        if claimed is None:
            break
        claimed_tasks.add(claimed.task)
    assert claimed_tasks == {LAB_DOCUMENT_CLEANUP_TASK, REPORT_ARTIFACT_CLEANUP_TASK}
    assert layout.path("tombstones", document_id, ".deleted").is_file()
    assert not (settings.report_artifacts_dir / str(owner_id) / str(report_id)).exists()
    assert (
        settings.report_artifacts_dir / ".tombstones" / str(owner_id) / f"{report_id}.deleted"
    ).is_file()


def test_scanned_pdf_ocr_path_remains_a_confirmation_required_draft(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    with Session(engine) as session:
        facts_before = session.scalar(select(func.count()).select_from(LabResult))
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={
            "file": (
                "synthetic-scan.pdf",
                synthetic_scanned_lab_pdf(),
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 202
    document_id = uuid.UUID(uploaded.json()["document_id"])
    process_available(DocumentLayout(settings.uploads_dir), runner=OcrToolRunner())

    extraction = client.get(f"/api/v1/labs/documents/{document_id}/extraction")
    assert extraction.status_code == 200, extraction.text
    candidates = extraction.json()["candidates"]
    parsed = [candidate for candidate in candidates if candidate["parsed"]]
    unparsed = [candidate for candidate in candidates if not candidate["parsed"]]
    assert parsed[0]["extraction_tier"] == "ocr"
    assert parsed[0]["coordinate_space"] == "rendered_pixels"
    assert parsed[0]["requires_confirmation"] is True
    assert "low_confidence" in unparsed[0]["flags"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabResult)) == facts_before


# ---------------------------------------------------------------------------
# SAFE-02: category discriminator
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-02")
def test_plan_resources_declare_the_plan_category(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/medications",
        json={"name": "Hydrocortisone", "default_unit": "mg", "default_route": "oral"},
        headers=logged_in,
    )
    assert created.status_code == 201, created.text
    assert created.json()["category"] == "plan"


@pytest.mark.safety("SAFE-02")
def test_fact_resources_declare_the_fact_category(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    medication_id = _a_medication(client, logged_in)
    dose = client.post(
        "/api/v1/doses",
        json={
            "medication_id": medication_id,
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": "2026-05-01T07:00:00", "timezone": "Europe/London"},
        },
        headers=logged_in,
    )
    assert dose.status_code == 201, dose.text
    assert dose.json()["category"] == "fact"


def test_dose_correction_is_typed_and_preserves_superseded_fact(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    original = _a_dose(client, logged_in)
    corrected = client.post(
        f"/api/v1/doses/{original['id']}/correct",
        json={
            "reason": "Synthetic transcription correction",
            "changes": {
                "amount": "10.1250",
                "time": {"local_time": "2026-05-01T07:15:00", "timezone": "Europe/London"},
            },
        },
        headers=logged_in,
    )
    assert corrected.status_code == 201, corrected.text
    body = corrected.json()
    assert body["amount"] == "10.1250"
    assert body["time"]["local_time"] == "2026-05-01T07:15:00"
    assert body["time"]["timezone"] == "Europe/London"
    assert body["provenance"]["supersedes_id"] == original["id"]
    assert body["provenance"]["correction_reason"] == "Synthetic transcription correction"

    current = client.get("/api/v1/doses").json()
    current_ids = {row["id"] for row in current["items"]}
    history_ids = {row["id"] for row in [*current["items"], *current["revisions"]]}
    assert body["id"] in current_ids
    assert original["id"] not in current_ids
    assert {original["id"], body["id"]} <= history_ids


def test_dose_correction_rejects_unknown_or_empty_changes(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    original = _a_dose(client, logged_in)
    unknown = client.post(
        f"/api/v1/doses/{original['id']}/correct",
        json={"reason": "Synthetic correction", "changes": {"medication_id": str(uuid.uuid4())}},
        headers=logged_in,
    )
    empty = client.post(
        f"/api/v1/doses/{original['id']}/correct",
        json={"reason": "Synthetic correction", "changes": {}},
        headers=logged_in,
    )
    assert unknown.status_code == 422
    assert empty.status_code == 422


def test_symptom_correction_preserves_typed_history(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    original = client.post(
        "/api/v1/symptoms",
        json={
            "name": "Synthetic fatigue",
            "severity": 4,
            "tracking_category": "postural",
            "time": {"local_time": "2021-05-03T09:00:00", "timezone": "Europe/London"},
        },
        headers=logged_in,
    ).json()
    response = client.post(
        f"/api/v1/symptoms/{original['id']}/correct",
        json={
            "reason": "Synthetic severity correction",
            "changes": {"severity": 6, "tracking_category": "mineralocorticoid"},
        },
        headers=logged_in,
    )
    assert response.status_code == 201, response.text
    corrected = response.json()
    assert corrected["severity"] == 6
    assert corrected["tracking_category"] == "mineralocorticoid"
    assert corrected["tracking_category_revision"] == "symptom-tracking-category-v1"
    assert corrected["provenance"]["supersedes_id"] == original["id"]
    symptom_page = client.get("/api/v1/symptoms").json()
    current_ids = {row["id"] for row in symptom_page["items"]}
    history_ids = {row["id"] for row in [*symptom_page["items"], *symptom_page["revisions"]]}
    assert corrected["id"] in current_ids
    assert original["id"] not in current_ids
    assert {original["id"], corrected["id"]} <= history_ids

    extra = client.post(
        "/api/v1/symptoms",
        json={
            "name": "Synthetic second paged symptom",
            "severity": 2,
            "time": {"local_time": "2021-05-04T09:00:00", "timezone": "Europe/London"},
        },
        headers=logged_in,
    ).json()
    page_params = {
        "page_size": 1,
        "date_from": "2021-05-03T00:00:00Z",
        "date_to": "2021-05-05T00:00:00Z",
    }
    first = client.get("/api/v1/symptoms", params=page_params).json()
    second = client.get("/api/v1/symptoms", params={**page_params, "page": 2}).json()
    assert first["page"] == {
        "page": 1,
        "page_size": 1,
        "total_items": 2,
        "total_pages": 2,
    }
    assert second["page"]["page"] == 2
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {
        corrected["id"],
        extra["id"],
    }
    assert {row["id"] for row in [*first["revisions"], *second["revisions"]]} == {original["id"]}
    assert client.get("/api/v1/symptoms", params={**page_params, "page": 3}).status_code == 422


def test_meals_are_owner_scoped_correctable_reference_context_without_pk_effect(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    day = date(2021, 6, 7)
    with Session(engine) as session:
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        curve_before = wake_pharmacokinetics.curve_for_owner(
            session,
            owner_id=owner_id,
            day=day,
            timezone="Europe/London",
        )

    original_response = client.post(
        "/api/v1/meal-events",
        json={
            "time": {"local_time": "2021-06-07T12:30:00", "timezone": "Europe/London"},
            "notes": "Synthetic observed lunch",
        },
        headers=logged_in,
    )
    assert original_response.status_code == 201, original_response.text
    original = original_response.json()
    assert original["size"] is None
    assert original["provenance"]["source_type"] == "web"
    assert original["provenance"]["confirmation_state"] == "direct"

    corrected_response = client.post(
        f"/api/v1/meal-events/{original['id']}/correct",
        json={
            "reason": "Synthetic size correction",
            "changes": {"size": "l"},
        },
        headers=logged_in,
    )
    assert corrected_response.status_code == 201, corrected_response.text
    corrected = corrected_response.json()
    assert corrected["size"] == "l"
    assert corrected["provenance"]["supersedes_id"] == original["id"]

    page = client.get(
        "/api/v1/meal-events",
        params={
            "local_date_from": day.isoformat(),
            "local_date_to": day.isoformat(),
            "timezone": "Europe/London",
        },
    ).json()
    assert [item["id"] for item in page["items"]] == [corrected["id"]]
    assert [item["id"] for item in page["revisions"]] == [original["id"]]

    timeline = client.get(
        "/api/v1/timeline",
        params={
            "types": "meal",
            "local_date_from": day.isoformat(),
            "local_date_to": day.isoformat(),
            "timezone": "Europe/London",
        },
    )
    assert timeline.status_code == 200, timeline.text
    assert timeline.json()["items"][0]["summary"] == "Meal · size L"

    with Session(engine) as session:
        assert owner_id is not None
        observed = wake_reference_inputs.observed_meals_for_day(
            session,
            owner_id=owner_id,
            day=day,
            timezone="Europe/London",
        )
        assert list(observed) == ["breakfast"]
        assert observed["breakfast"].hour == 12
        assert observed["breakfast"].minute == 30
        reference = wake_reference_inputs.reference_for_owner(
            session,
            owner_id=owner_id,
            day=day,
            timezone="Europe/London",
            wake_at=datetime(2021, 6, 7, 6, tzinfo=UTC),
            sleep_onset_at=datetime(2021, 6, 6, 22, tzinfo=UTC),
        )
        assumptions = cast(dict[str, object], reference["assumptions"])
        meals = cast(dict[str, datetime], assumptions["observed_meals"])
        assert list(meals) == ["breakfast"]
        assert meals["breakfast"] == observed["breakfast"]
        projection = day_analysis_service.build_projection(
            session,
            owner_id=owner_id,
            day=day,
            timezone="Europe/London",
        )
        recorded_context = cast(dict[str, object], projection["recorded_facts_and_plan_context"])
        projected_meals = cast(list[dict[str, object]], recorded_context["meals"])
        assert projected_meals == [
            {
                "id": corrected["id"],
                "occurred_at": "2021-06-07T11:30:00+00:00",
                "local_time": "2021-06-07T12:30:00+01:00",
                "local_hour": 12,
                "local_minute": 30,
                "timezone": "Europe/London",
                "size": "l",
                "notes": "Synthetic observed lunch",
            }
        ]
        curve_after = wake_pharmacokinetics.curve_for_owner(
            session,
            owner_id=owner_id,
            day=day,
            timezone="Europe/London",
        )
        assert curve_after == curve_before


def test_sensitive_diary_and_life_events_require_explicit_reveal(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    time_payload = {"local_time": "2026-05-04T10:00:00", "timezone": "Europe/London"}
    private_diary = client.post(
        "/api/v1/diary-events",
        json={"text": "Synthetic private note", "is_sensitive": True, "time": time_payload},
        headers=logged_in,
    ).json()
    private_life = client.post(
        "/api/v1/life-events",
        json={
            "title": "Synthetic private event",
            "category": "other",
            "is_sensitive": True,
            "time": time_payload,
        },
        headers=logged_in,
    ).json()
    assert private_life["is_sensitive"] is True

    default_diary = client.get("/api/v1/diary-events").json()
    default_life = client.get("/api/v1/life-events").json()
    default_diary_ids = {row["id"] for row in default_diary["items"]}
    default_life_ids = {row["id"] for row in default_life["items"]}
    assert default_diary["revisions"] == []
    assert default_life["revisions"] == []
    revealed_diary_ids = {
        row["id"]
        for row in client.get("/api/v1/diary-events", params={"include_sensitive": True}).json()[
            "items"
        ]
    }
    revealed_life_ids = {
        row["id"]
        for row in client.get("/api/v1/life-events", params={"include_sensitive": True}).json()[
            "items"
        ]
    }
    assert private_diary["id"] not in default_diary_ids
    assert private_life["id"] not in default_life_ids
    assert private_diary["id"] in revealed_diary_ids
    assert private_life["id"] in revealed_life_ids


@pytest.mark.safety("SAFE-02")
def test_timeline_carries_a_category_per_item(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    _a_dose(client, logged_in)
    items = client.get("/api/v1/timeline").json()["items"]
    assert items
    assert all(item["category"] in {"fact", "plan", "ai"} for item in items)


def test_timeline_local_date_filter_uses_the_requested_timezone(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    _a_dose(client, logged_in)
    included = client.get(
        "/api/v1/timeline",
        params={
            "local_date_from": "2026-05-01",
            "local_date_to": "2026-05-01",
            "timezone": "Europe/London",
        },
    ).json()
    excluded = client.get(
        "/api/v1/timeline",
        params={
            "local_date_from": "2026-05-02",
            "local_date_to": "2026-05-02",
            "timezone": "Europe/London",
        },
    ).json()
    assert included["items"]
    assert excluded["items"] == []


def test_health_data_local_date_filters_compose_with_pagination_and_corrections(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    def blood_pressure(local_time: str, systolic: int) -> str:
        response = client.post(
            "/api/v1/blood-pressure",
            json={
                "systolic_mmhg": systolic,
                "diastolic_mmhg": 70,
                "time": {"local_time": local_time, "timezone": "UTC"},
            },
            headers=logged_in,
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    # New York's 2026 spring-forward day spans 05:00Z through 03:59:59Z the next day.
    at_start = blood_pressure("2026-03-08T05:00:00", 110)
    near_end = blood_pressure("2026-03-09T03:59:00", 111)
    outside = blood_pressure("2026-03-09T04:00:00", 112)
    weight = client.post(
        "/api/v1/weight",
        json={
            "value": "180",
            "unit": "lb",
            "time": {"local_time": "2026-03-09T03:00:00", "timezone": "UTC"},
        },
        headers=logged_in,
    )
    assert weight.status_code == 201, weight.text
    with Session(engine) as session, session.begin():
        other_owner = Owner(
            email="health-filter-other-owner@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(other_owner)
        session.flush()
        other_record = events.create_event(
            session,
            BloodPressureEvent,
            owner_id=other_owner.id,
            event_time=resolve_event_time(datetime(2026, 3, 8, 6), "UTC"),  # noqa: DTZ001
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            systolic_mmhg=199,
            diastolic_mmhg=99,
        )
        other_record_id = str(other_record.id)

    params = {
        "local_date_from": "2026-03-08",
        "local_date_to": "2026-03-08",
        "timezone": "America/New_York",
        "page_size": 1,
    }
    first = client.get("/api/v1/blood-pressure", params=params)
    second = client.get("/api/v1/blood-pressure", params={**params, "page": 2})
    assert first.status_code == second.status_code == 200
    assert first.json()["page"] == {
        "page": 1,
        "page_size": 1,
        "total_items": 2,
        "total_pages": 2,
    }
    assert {first.json()["items"][0]["id"], second.json()["items"][0]["id"]} == {
        at_start,
        near_end,
    }
    assert outside not in {first.json()["items"][0]["id"], second.json()["items"][0]["id"]}
    assert other_record_id not in {
        first.json()["items"][0]["id"],
        second.json()["items"][0]["id"],
    }
    assert (
        client.get(
            "/api/v1/weight",
            params={key: value for key, value in params.items() if key != "page_size"},
        ).json()["items"][0]["id"]
        == weight.json()["id"]
    )
    from_only = client.get(
        "/api/v1/blood-pressure",
        params={"local_date_from": "2026-03-09", "timezone": "America/New_York"},
    )
    through_only = client.get(
        "/api/v1/blood-pressure",
        params={"local_date_to": "2026-03-07", "timezone": "America/New_York"},
    )
    assert from_only.status_code == through_only.status_code == 200
    assert outside in {row["id"] for row in from_only.json()["items"]}
    assert at_start not in {row["id"] for row in from_only.json()["items"]}
    assert through_only.json()["items"] == []

    corrected = client.post(
        f"/api/v1/blood-pressure/{at_start}/correct",
        json={
            "reason": "Synthetic correction within selected local date",
            "changes": {"systolic_mmhg": 109},
        },
        headers=logged_in,
    )
    assert corrected.status_code == 201, corrected.text
    corrected_page = client.get("/api/v1/blood-pressure", params={**params, "page_size": 25}).json()
    assert at_start not in {row["id"] for row in corrected_page["items"]}
    assert corrected.json()["id"] in {row["id"] for row in corrected_page["items"]}
    assert at_start in {row["id"] for row in corrected_page["revisions"]}

    invalid_range = client.get(
        "/api/v1/weight",
        params={
            "local_date_from": "2026-03-09",
            "local_date_to": "2026-03-08",
            "timezone": "UTC",
        },
    )
    invalid_timezone = client.get("/api/v1/blood-pressure", params={"timezone": "Not/AZone"})
    assert invalid_range.status_code == invalid_timezone.status_code == 422
    assert invalid_range.json()["detail"]["code"] == "invalid_local_date_range"
    assert invalid_timezone.json()["detail"]["code"] == "invalid_timezone"

    with Session(engine) as session:
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
    with Session(engine) as session, session.begin():
        session.add(
            GarminConnection(
                owner_id=owner_id,
                state=GarminConnectionState.CONNECTED,
                connected_at=datetime(2026, 3, 8, 5, tzinfo=UTC),
                capabilities={},
                client_version="synthetic-filter-test",
            )
        )
    mapped = map_day(
        day=date(2026, 3, 8),
        stats={"totalSteps": 1234},
        sleep={},
        timezone="America/New_York",
    )
    fetched = FetchedWindow(
        start_date=date(2026, 3, 8),
        end_date=date(2026, 3, 8),
        timezone="America/New_York",
        metrics=mapped.metrics,
        intraday_metrics=(),
        sleeps=(),
        activities=(),
        warnings=mapped.warnings,
        capabilities=mapped.capabilities,
        started_at=datetime(2026, 3, 9, 5, tzinfo=UTC),
        finished_at=datetime(2026, 3, 9, 5, 0, 1, tzinfo=UTC),
    )
    with Session(engine) as session, session.begin():
        persist_window(session, owner_id=owner_id, fetched=fetched)
    garmin_included = client.get("/api/v1/integrations/garmin/records", params=params)
    garmin_excluded = client.get(
        "/api/v1/integrations/garmin/records",
        params={
            "local_date_from": "2026-03-09",
            "local_date_to": "2026-03-09",
            "timezone": "America/New_York",
        },
    )
    assert garmin_included.status_code == garmin_excluded.status_code == 200
    assert any(row["metric_type"] == "steps" for row in garmin_included.json()["records"])
    assert garmin_excluded.json()["records"] == []


def test_symptom_diary_and_life_local_date_filters_preserve_sensitive_boundaries(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    def event_time(local_time: str) -> dict[str, str]:
        return {"local_time": local_time, "timezone": "UTC"}

    symptom = client.post(
        "/api/v1/symptoms",
        json={"name": "Synthetic boundary symptom", "time": event_time("2026-03-08T05:00:00")},
        headers=logged_in,
    )
    diary = client.post(
        "/api/v1/diary-events",
        json={
            "text": "Synthetic sensitive boundary diary",
            "is_sensitive": True,
            "time": event_time("2026-03-09T03:59:00"),
        },
        headers=logged_in,
    )
    life = client.post(
        "/api/v1/life-events",
        json={
            "title": "Synthetic boundary life event",
            "category": "other",
            "is_sensitive": False,
            "time": event_time("2026-03-09T03:59:00"),
        },
        headers=logged_in,
    )
    outside = client.post(
        "/api/v1/symptoms",
        json={"name": "Synthetic outside symptom", "time": event_time("2026-03-09T04:00:00")},
        headers=logged_in,
    )
    assert all(response.status_code == 201 for response in (symptom, diary, life, outside))

    params = {
        "local_date_from": "2026-03-08",
        "local_date_to": "2026-03-08",
        "timezone": "America/New_York",
    }
    symptom_page = client.get("/api/v1/symptoms", params=params)
    diary_hidden = client.get("/api/v1/diary-events", params=params)
    diary_revealed = client.get(
        "/api/v1/diary-events", params={**params, "include_sensitive": True}
    )
    life_page = client.get("/api/v1/life-events", params=params)
    assert all(
        response.status_code == 200
        for response in (symptom_page, diary_hidden, diary_revealed, life_page)
    )
    assert symptom.json()["id"] in {row["id"] for row in symptom_page.json()["items"]}
    assert outside.json()["id"] not in {row["id"] for row in symptom_page.json()["items"]}
    assert diary.json()["id"] not in {row["id"] for row in diary_hidden.json()["items"]}
    assert diary.json()["id"] in {row["id"] for row in diary_revealed.json()["items"]}
    assert life.json()["id"] in {row["id"] for row in life_page.json()["items"]}

    from_only = client.get(
        "/api/v1/symptoms",
        params={"local_date_from": "2026-03-09", "timezone": "America/New_York"},
    )
    invalid = client.get(
        "/api/v1/life-events",
        params={
            "local_date_from": "2026-03-09",
            "local_date_to": "2026-03-08",
            "timezone": "UTC",
        },
    )
    assert outside.json()["id"] in {row["id"] for row in from_only.json()["items"]}
    assert symptom.json()["id"] not in {row["id"] for row in from_only.json()["items"]}
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_local_date_range"


def test_dose_episode_injection_and_context_local_date_filters_share_dst_boundaries(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    medication_id = _a_medication(client, logged_in)
    boundary_time = {"local_time": "2024-03-10T05:00:00", "timezone": "UTC"}
    late_time = {"local_time": "2024-03-11T03:59:00", "timezone": "UTC"}
    outside_time = {"local_time": "2024-03-11T04:00:00", "timezone": "UTC"}

    dose = client.post(
        "/api/v1/doses",
        headers=logged_in,
        json={
            "medication_id": medication_id,
            "amount": "5",
            "unit": "mg",
            "route": "oral",
            "category": "stress",
            "time": boundary_time,
        },
    )
    episode = client.post(
        "/api/v1/stress-episodes",
        headers=logged_in,
        json={"trigger": "Synthetic DST episode", "time": boundary_time},
    )
    overlapping_episode = client.post(
        "/api/v1/stress-episodes",
        headers=logged_in,
        json={
            "trigger": "Synthetic episode crossing local midnight",
            "time": {"local_time": "2024-03-10T04:00:00", "timezone": "UTC"},
        },
    )
    assert overlapping_episode.status_code == 201
    resolved_overlap = client.patch(
        f"/api/v1/stress-episodes/{overlapping_episode.json()['id']}",
        headers=logged_in,
        json={
            "status": "resolved",
            "ended_at": {"local_time": "2024-03-10T06:00:00", "timezone": "UTC"},
        },
    )
    assert resolved_overlap.status_code == 200
    injection = client.post(
        "/api/v1/emergency-injections",
        headers=logged_in,
        json={
            "medication_id": medication_id,
            "amount": "100",
            "unit": "mg",
            "route": "intramuscular",
            "time": late_time,
        },
    )
    context = client.post(
        "/api/v1/context-events",
        headers=logged_in,
        json={"time": late_time, "location_precision": "none"},
    )
    outside = client.post(
        "/api/v1/doses",
        headers=logged_in,
        json={
            "medication_id": medication_id,
            "amount": "6",
            "unit": "mg",
            "route": "oral",
            "category": "stress",
            "time": outside_time,
        },
    )
    assert all(
        response.status_code == 201 for response in (dose, episode, injection, context, outside)
    )

    params = {
        "local_date_from": "2024-03-10",
        "local_date_to": "2024-03-10",
        "timezone": "America/New_York",
    }
    paths = {
        "/api/v1/doses": dose.json()["id"],
        "/api/v1/stress-episodes": episode.json()["id"],
        "/api/v1/emergency-injections": injection.json()["id"],
        "/api/v1/context-events": context.json()["id"],
    }
    for path, expected_id in paths.items():
        response = client.get(path, params=params)
        assert response.status_code == 200, response.text
        assert expected_id in {row["id"] for row in response.json()["items"]}
    ordinary_episode_ids = {
        row["id"] for row in client.get("/api/v1/stress-episodes", params=params).json()["items"]
    }
    overlapping_episode_ids = {
        row["id"]
        for row in client.get(
            "/api/v1/stress-episodes", params={**params, "overlaps_window": True}
        ).json()["items"]
    }
    assert overlapping_episode.json()["id"] not in ordinary_episode_ids
    assert overlapping_episode.json()["id"] in overlapping_episode_ids
    dose_ids = {row["id"] for row in client.get("/api/v1/doses", params=params).json()["items"]}
    assert outside.json()["id"] not in dose_ids

    through_only = client.get(
        "/api/v1/emergency-injections",
        params={"local_date_to": "2024-03-10", "timezone": "America/New_York"},
    )
    assert injection.json()["id"] in {row["id"] for row in through_only.json()["items"]}
    invalid = client.get(
        "/api/v1/context-events",
        params={
            "local_date_from": "2024-03-11",
            "local_date_to": "2024-03-10",
            "timezone": "UTC",
        },
    )
    assert invalid.status_code == 422
    assert invalid.json()["detail"]["code"] == "invalid_local_date_range"


def test_plan_lab_and_report_history_filters_use_domain_dates_and_dst_boundaries(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    boundary = "2024-03-10T05:00:00"
    outside = "2024-03-11T04:00:00"
    regimens = []
    for label, effective_from in (
        ("Synthetic DST boundary plan", boundary),
        ("Synthetic next-day plan", outside),
    ):
        response = client.post(
            "/api/v1/regimens",
            headers=logged_in,
            json={
                "version_label": label,
                "effective_from": effective_from,
                "slots": [],
                "instructions": [],
            },
        )
        assert response.status_code == 201, response.text
        regimens.append(response.json()["id"])

    panels = []
    for label, local_time in (
        ("Synthetic boundary analyte", boundary),
        ("Synthetic outside analyte", outside),
    ):
        response = client.post(
            "/api/v1/labs/manual",
            headers=logged_in,
            json={
                "specimen_time": {"local_time": local_time, "timezone": "UTC"},
                "report_time": {"local_time": local_time, "timezone": "UTC"},
                "results": [{"analyte_name": label, "qualitative_result": "Synthetic"}],
            },
        )
        assert response.status_code == 201, response.text
        panels.append(response.json()["panel_id"])

    report = client.post(
        "/api/v1/reports",
        headers=logged_in,
        json={
            "date_from": "2024-03-10",
            "date_to": "2024-03-10",
            "timezone": "America/New_York",
            "selected_sections": ["metrics"],
        },
    )
    assert report.status_code == 201, report.text
    report_id = report.json()["id"]
    with Session(engine) as session, session.begin():
        snapshot = session.get(ReportSnapshot, uuid.UUID(report_id))
        assert snapshot is not None
        snapshot.created_at = datetime(2024, 3, 10, 5, tzinfo=UTC)

    params = {
        "local_date_from": "2024-03-10",
        "local_date_to": "2024-03-10",
        "timezone": "America/New_York",
    }
    plan_ids = {
        item["id"] for item in client.get("/api/v1/regimens", params=params).json()["items"]
    }
    assert regimens[0] in plan_ids
    assert regimens[1] not in plan_ids
    lab_panel_ids = {
        item["panel_id"]
        for item in client.get("/api/v1/labs/results", params=params).json()["items"]
    }
    assert panels[0] in lab_panel_ids
    assert panels[1] not in lab_panel_ids
    report_ids = {
        item["id"] for item in client.get("/api/v1/reports", params=params).json()["items"]
    }
    assert report_id in report_ids

    invalid_params = {
        "local_date_from": "2024-03-11",
        "local_date_to": "2024-03-10",
        "timezone": "America/New_York",
    }
    for path in (
        "/api/v1/regimens",
        "/api/v1/labs/results",
        "/api/v1/labs/documents",
        "/api/v1/reports",
    ):
        invalid = client.get(path, params=invalid_params)
        assert invalid.status_code == 422, (path, invalid.text)
        assert invalid.json()["detail"]["code"] == "invalid_local_date_range"


def test_timeline_orders_by_experienced_time_with_stable_ties(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    """Insertion time never controls chronology; equal instants use type then id."""

    def symptom(name: str, local_time: str) -> str:
        response = client.post(
            "/api/v1/symptoms",
            json={
                "name": name,
                "severity": 1,
                "time": {"local_time": local_time, "timezone": "UTC"},
            },
            headers=logged_in,
        )
        assert response.status_code == 201, response.text
        return response.json()["id"]

    # Deliberately insert the events in a different order from experienced time.
    late_id = symptom("Synthetic late timeline event", "2024-02-03T21:00:00")
    first_tie_id = symptom("Synthetic first tied timeline event", "2024-02-03T12:00:00")
    early_id = symptom("Synthetic early timeline event", "2024-02-03T07:00:00")
    second_tie_id = symptom("Synthetic second tied timeline event", "2024-02-03T12:00:00")
    tie_ids = sorted([first_tie_id, second_tie_id])
    params = {
        "types": "symptom",
        "local_date_from": "2024-02-03",
        "local_date_to": "2024-02-03",
        "timezone": "UTC",
    }

    default_descending = client.get("/api/v1/timeline", params=params)
    assert default_descending.status_code == 200, default_descending.text
    assert [item["id"] for item in default_descending.json()["items"]] == [
        late_id,
        *tie_ids,
        early_id,
    ]

    ascending = client.get("/api/v1/timeline", params={**params, "sort_order": "asc"})
    assert ascending.status_code == 200, ascending.text
    assert [item["id"] for item in ascending.json()["items"]] == [
        early_id,
        *tie_ids,
        late_id,
    ]

    descending = client.get("/api/v1/timeline", params={**params, "sort_order": "desc"})
    assert descending.status_code == 200, descending.text
    assert [item["id"] for item in descending.json()["items"]] == [
        late_id,
        *tie_ids,
        early_id,
    ]

    invalid = client.get("/api/v1/timeline", params={**params, "sort_order": "recorded_at"})
    assert invalid.status_code == 422

    correction = client.post(
        f"/api/v1/symptoms/{early_id}/correct",
        json={
            "reason": "Synthetic experienced-time correction",
            "changes": {"time": {"local_time": "2024-02-03T22:00:00", "timezone": "UTC"}},
        },
        headers=logged_in,
    )
    assert correction.status_code == 201, correction.text
    corrected_id = correction.json()["id"]
    corrected_timeline = client.get("/api/v1/timeline", params=params).json()["items"]
    assert corrected_timeline[0]["id"] == corrected_id
    assert corrected_timeline[0]["provenance"]["is_correction"] is True
    assert early_id not in {item["id"] for item in corrected_timeline}


def test_timeline_has_bounded_pages_and_rejects_out_of_range_pages(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    created_ids: list[str] = []
    for hour in range(21):
        response = client.post(
            "/api/v1/symptoms",
            json={
                "name": f"Synthetic paged symptom {hour}",
                "severity": 1,
                "time": {"local_time": f"2024-03-04T{hour:02d}:00:00", "timezone": "UTC"},
            },
            headers=logged_in,
        )
        assert response.status_code == 201, response.text
        created_ids.append(response.json()["id"])

    params = {
        "types": "symptom",
        "local_date_from": "2024-03-04",
        "local_date_to": "2024-03-04",
        "timezone": "UTC",
        "page_size": 10,
    }
    first = client.get("/api/v1/timeline", params=params)
    middle = client.get("/api/v1/timeline", params={**params, "page": 2})
    last = client.get("/api/v1/timeline", params={**params, "page": 3})

    assert first.status_code == middle.status_code == last.status_code == 200
    assert first.json()["page"] == {
        "page": 1,
        "page_size": 10,
        "total_items": 21,
        "total_pages": 3,
    }
    assert middle.json()["page"]["page"] == 2
    assert last.json()["page"]["page"] == 3
    assert len(first.json()["items"]) == len(middle.json()["items"]) == 10
    assert len(last.json()["items"]) == 1
    assert [item["id"] for item in first.json()["items"]] == list(reversed(created_ids[-10:]))
    assert last.json()["items"][0]["id"] == created_ids[0]

    out_of_range = client.get("/api/v1/timeline", params={**params, "page": 4})
    oversized = client.get("/api/v1/timeline", params={**params, "page_size": 101})
    assert out_of_range.status_code == 422
    assert out_of_range.json()["detail"]["code"] == "page_out_of_range"
    assert oversized.status_code == 422


# ---------------------------------------------------------------------------
# Generated-analysis persistence boundaries
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-05")
def test_database_rejects_uncited_ai_analysis(engine: Engine) -> None:
    with Session(engine) as session:
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        session.add(
            AIAnalysis(
                owner_id=owner_id,
                analysis_type=AnalysisType.PATTERN_OBSERVATION,
                body="Synthetic uncited analysis that must not persist.",
                source_record_ids=[],
                computed_inputs={"synthetic_value": 1},
                model_name="synthetic-model",
                model_digest="a" * 64,
                prompt_version="synthetic-v1",
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()


@pytest.mark.safety("SAFE-06")
def test_generate_and_delete_ai_analysis_leaves_fact_and_plan_rows_bit_identical(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    medication_id = _a_medication(client, logged_in)
    plan = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            "version_label": "Synthetic analysis isolation plan",
            "effective_from": "2025-01-01T00:00:00Z",
            "slots": [],
            "instructions": [],
        },
    )
    assert plan.status_code == 201, plan.text
    dose = client.post(
        "/api/v1/doses",
        headers=logged_in,
        json={
            "medication_id": medication_id,
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": "2025-01-01T07:00:00", "timezone": "UTC"},
        },
    )
    assert dose.status_code == 201, dose.text
    plan_id = uuid.UUID(plan.json()["id"])
    dose_id = uuid.UUID(dose.json()["id"])

    fake_client = mock.Mock(spec=OllamaClient)
    fake_client.generate_json.return_value = ModelResult(
        outcome=ModelOutcome.OK,
        data={
            "refused": False,
            "refusal_reason": None,
            "claims": [
                {
                    "text": "The synthetic recorded total was 10 mg.",
                    "source_record_ids": [str(dose_id)],
                    "numeric_values": ["10.0000"],
                }
            ],
            "missingness": "The deterministic input reports 0 missing records.",
            "correlation_caution": "This description does not establish causation.",
        },
        model_name="synthetic-model",
        model_digest="a" * 64,
    )
    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        fact_before = dict(
            session.execute(select(DoseEvent.__table__).where(DoseEvent.id == dose_id))
            .mappings()
            .one()
        )
        plan_before = dict(
            session.execute(select(RegimenVersion.__table__).where(RegimenVersion.id == plan_id))
            .mappings()
            .one()
        )
        generated = analysis_service.generate_analysis(
            session,
            owner_id=owner_id,
            analysis_type=AnalysisType.DAILY_SUMMARY,
            source_record_ids=[str(dose_id)],
            computed_inputs={"recorded_total_mg": "10.0000", "missing_records": 0},
            client=cast(OllamaClient, fake_client),
            persisted_source_record_ids=[str(dose_id), str(plan_id)],
            persisted_inputs={"source_revision_sha256": "b" * 64},
        )
        assert generated.outcome is analysis_service.AnalysisOutcome.CREATED
        assert generated.analysis is not None
        assert generated.analysis.source_record_ids == [str(dose_id), str(plan_id)]
        assert generated.analysis.computed_inputs == {"source_revision_sha256": "b" * 64}
        session.delete(generated.analysis)
        session.flush()
        fact_after = dict(
            session.execute(select(DoseEvent.__table__).where(DoseEvent.id == dose_id))
            .mappings()
            .one()
        )
        plan_after = dict(
            session.execute(select(RegimenVersion.__table__).where(RegimenVersion.id == plan_id))
            .mappings()
            .one()
        )
        assert fact_after == fact_before
        assert plan_after == plan_before


# ---------------------------------------------------------------------------
# Development-only medication plan deletion
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("plan_status", ["draft", "approved", "retired"])
def test_development_plan_deletion_preserves_facts_and_handles_derived_references(
    plan_status: str, client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    medication_id = _a_medication(client, logged_in)
    year = {"draft": 1981, "approved": 1982, "retired": 1983}[plan_status]
    created = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            "version_label": f"Synthetic disposable {plan_status} plan",
            "effective_from": f"{year}-01-01T00:00:00",
            "effective_to": f"{year}-01-02T00:00:00",
            "slots": [
                {
                    "medication_id": medication_id,
                    "scheduled_local_time": "07:00:00",
                    "amount": "10",
                    "unit": "mg",
                    "route": "oral",
                    "sort_order": 0,
                }
            ],
            "instructions": [
                {
                    "category": "general",
                    "title": "Synthetic instruction",
                    "body": "Synthetic physician-authored test content.",
                    "authored_by": "Dr Synthetic",
                    "authored_on": f"{year - 1}-12-01",
                    "sort_order": 0,
                }
            ],
        },
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["id"]
    assert created.json()["deletion_allowed"] is True
    slot_id = created.json()["slots"][0]["id"]
    if plan_status != "draft":
        approved = client.post(
            f"/api/v1/regimens/{version_id}/approve",
            headers=logged_in,
            json={
                "approved_by": "Dr Synthetic",
                "approval_source": "Synthetic historical letter",
                "approved_at": f"{year}-01-01T00:00:00Z",
            },
        )
        assert approved.status_code == 200, approved.text
        if plan_status == "retired":
            retired = client.post(f"/api/v1/regimens/{version_id}/retire", headers=logged_in)
            assert retired.status_code == 200, retired.text

    dose = client.post(
        "/api/v1/doses",
        headers=logged_in,
        json={
            "medication_id": medication_id,
            "amount": "1",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": f"{year}-01-01T07:00:00", "timezone": "UTC"},
        },
    )
    assert dose.status_code == 201, dose.text
    dose_id = uuid.UUID(dose.json()["id"])

    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        dose_row = session.get(DoseEvent, dose_id)
        assert dose_row is not None
        dose_row.regimen_version_id = uuid.UUID(version_id)
        dose_row.slot_id = uuid.UUID(slot_id)
        analysis = AIAnalysis(
            owner_id=owner_id,
            analysis_type=AnalysisType.PATTERN_OBSERVATION,
            body="Synthetic referenced analysis.",
            source_record_ids=[version_id],
            computed_inputs={"plan": {"id": version_id}},
            model_name="synthetic-model",
            model_digest="a" * 64,
            prompt_version="synthetic-v1",
        )
        session.add(analysis)
        snapshot = report_service.create_snapshot(
            session,
            owner_id=owner_id,
            date_from=date(year, 1, 1),
            date_to=date(year, 1, 2),
            timezone="UTC",
            selected_sections=["approved_plan"],
            include_ai=False,
            source_manifest={
                "fact": [],
                "plan": [version_id],
                "patient_note": [],
                "ai": [],
            },
            metric_values={},
            snapshot_content={
                "fact": [],
                "plan": [{"regimen_version_id": version_id}],
                "patient_note": [],
                "ai": [],
            },
        )
        session.flush()
        analysis_id = analysis.id
        snapshot_id = snapshot.id

    deleted = client.delete(f"/api/v1/regimens/{version_id}", headers=logged_in)
    assert deleted.status_code == 204, deleted.text

    with Session(engine) as session:
        target = uuid.UUID(version_id)
        assert session.get(RegimenVersion, target) is None
        assert (
            session.scalar(
                select(func.count())
                .select_from(RegimenDoseSlot)
                .where(RegimenDoseSlot.regimen_version_id == target)
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(ApprovedInstruction)
                .where(ApprovedInstruction.regimen_version_id == target)
            )
            == 0
        )
        retained_dose = session.get(DoseEvent, dose_id)
        assert retained_dose is not None
        assert retained_dose.regimen_version_id is None
        assert retained_dose.slot_id is None
        assert session.get(Medication, uuid.UUID(medication_id)) is not None
        invalid_analysis = session.get(AIAnalysis, analysis_id)
        assert invalid_analysis is not None
        assert invalid_analysis.hidden_at is not None
        assert session.get(ReportSnapshot, snapshot_id) is not None
        entry = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == AuditAction.REGIMEN_DELETED,
                AuditEntry.target_id == target,
            )
        )
        assert entry is not None
        assert entry.change_summary is not None
        assert f"status={plan_status}" in entry.change_summary
        assert "Synthetic" not in entry.change_summary
        assert "Dr" not in entry.change_summary


def test_development_plan_deletion_is_csrf_owner_scoped_and_disabled_in_production(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    own = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            "version_label": "Synthetic protected plan",
            "effective_from": "2044-01-01T00:00:00",
            "slots": [],
            "instructions": [],
        },
    )
    assert own.status_code == 201, own.text
    own_id = uuid.UUID(own.json()["id"])
    assert client.delete(f"/api/v1/regimens/{own_id}").status_code == 403

    with Session(engine) as session, session.begin():
        other = Owner(
            email="other-synthetic-owner@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(other)
        session.flush()
        other_draft = medication_service.create_draft(
            session,
            owner_id=other.id,
            version_label="Other owner's synthetic draft",
            effective_from=datetime(2038, 1, 1, tzinfo=UTC),
        )
        other_id = other_draft.id

    hidden = client.request(
        "DELETE",
        f"/api/v1/regimens/{other_id}",
        headers=logged_in,
    )
    assert hidden.status_code == 404

    app = cast(FastAPI, client.app)
    development_settings = app.state.settings
    app.state.settings = development_settings.model_copy(update={"environment": Environment.PROD})
    try:
        listed = client.get("/api/v1/regimens")
        assert listed.status_code == 200
        own_payload = next(row for row in listed.json()["items"] if row["id"] == str(own_id))
        assert own_payload["deletion_allowed"] is False
        refused = client.delete(f"/api/v1/regimens/{own_id}", headers=logged_in)
        assert refused.status_code == 403
        assert refused.json()["detail"] == "plan deletion is available only in development"
    finally:
        app.state.settings = development_settings

    with Session(engine) as session:
        assert session.get(RegimenVersion, other_id) is not None
        assert session.get(RegimenVersion, own_id) is not None

    assert client.delete(f"/api/v1/regimens/{own_id}", headers=logged_in).status_code == 204


# ---------------------------------------------------------------------------
# Development-only exact synthetic medication bootstrap cleanup
# ---------------------------------------------------------------------------


def test_exact_synthetic_bootstrap_preview_confirmation_cleanup_and_audit(
    client: TestClient, engine: Engine
) -> None:
    _ = client
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection, expire_on_commit=False) as session:
            owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
            assert owner_id is not None
            target = _legacy_medication_bootstrap(session, owner_id=owner_id, approved=True)
            unrelated = Medication(
                owner_id=owner_id,
                name="Unrelated synthetic medicine",
                normalized_name="unrelated synthetic medicine",
                strength=Decimal("1"),
                default_unit=DoseUnit.MG,
                default_route=Route.ORAL,
            )
            session.add(unrelated)
            session.flush()
            unrelated_id = unrelated.id
            session.expire_all()

            preview = preview_synthetic_bootstrap(session, owner_id=owner_id)
            assert preview.regimen_version_ids == (target["regimen_id"],)
            assert preview.counts.regimen_versions == 1
            assert preview.counts.regimen_dose_slots == 4
            assert preview.counts.approved_instructions == 2
            assert preview.counts.medications == 3
            assert preview.references.total == 0
            assert preview.confirmation_phrase.startswith("PURGE SYNTHETIC BOOTSTRAP ")

            with pytest.raises(SyntheticBootstrapCleanupError, match="confirmation"):
                execute_synthetic_bootstrap_cleanup(
                    session,
                    owner_id=owner_id,
                    preview=preview,
                    confirmation="PURGE SOMETHING ELSE",
                )
            assert session.get(RegimenVersion, target["regimen_id"]) is not None

            counts = execute_synthetic_bootstrap_cleanup(
                session,
                owner_id=owner_id,
                preview=preview,
                confirmation=preview.confirmation_phrase,
            )
            session.flush()
            assert counts == preview.counts
            assert session.get(RegimenVersion, target["regimen_id"]) is None
            assert all(
                session.get(Medication, row_id) is None for row_id in target["medication_ids"]
            )
            assert session.get(Medication, unrelated_id) is not None
            entry = session.scalar(
                select(AuditEntry).where(
                    AuditEntry.action == AuditAction.SYNTHETIC_MEDICATION_BOOTSTRAP_PURGED,
                    AuditEntry.target_id == target["regimen_id"],
                )
            )
            assert entry is not None
            assert entry.change_summary is not None
            assert "Hydrocortisone" not in entry.change_summary
            assert "Dr Example" not in entry.change_summary
            assert "clinic letter" not in entry.change_summary
    finally:
        transaction.rollback()
        connection.close()


@pytest.mark.parametrize(
    "reference_kind",
    ["dose", "injection", "other_plan", "report", "analysis", "draft", "document"],
)
def test_synthetic_bootstrap_cleanup_refuses_every_retained_reference(
    reference_kind: str, client: TestClient, engine: Engine
) -> None:
    _ = client
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection, expire_on_commit=False) as session:
            owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
            assert owner_id is not None
            target = _legacy_medication_bootstrap(session, owner_id=owner_id, approved=False)
            regimen = session.get(RegimenVersion, target["regimen_id"])
            medication = session.get(Medication, target["medication_ids"][0])
            slot = session.get(RegimenDoseSlot, target["slot_ids"][0])
            assert regimen is not None and medication is not None and slot is not None
            target_id = str(regimen.id)
            if reference_kind == "dose":
                events.create_event(
                    session,
                    DoseEvent,
                    owner_id=owner_id,
                    event_time=events.build_event_time(datetime(2026, 1, 2, 7, tzinfo=UTC), "UTC"),
                    source_type=SourceType.WEB,
                    confirmation_state=ConfirmationState.DIRECT,
                    medication_id=medication.id,
                    amount=Decimal("1"),
                    unit=DoseUnit.MG,
                    route=Route.ORAL,
                    category="scheduled",
                    regimen_version_id=regimen.id,
                    slot_id=slot.id,
                )
            elif reference_kind == "injection":
                from healthcurve.episodes.models import EmergencyInjectionEvent

                events.create_event(
                    session,
                    EmergencyInjectionEvent,
                    owner_id=owner_id,
                    event_time=events.build_event_time(datetime(2026, 1, 2, 7, tzinfo=UTC), "UTC"),
                    source_type=SourceType.WEB,
                    confirmation_state=ConfirmationState.DIRECT,
                    medication_id=medication.id,
                    amount=Decimal("1"),
                    unit="mg",
                    route="intramuscular",
                )
            elif reference_kind == "other_plan":
                other = medication_service.create_draft(
                    session,
                    owner_id=owner_id,
                    version_label="Unrelated synthetic draft",
                    effective_from=datetime(2035, 1, 1, tzinfo=UTC),
                )
                session.add(
                    RegimenDoseSlot(
                        regimen_version_id=other.id,
                        medication_id=medication.id,
                        scheduled_local_time=datetime.min.time(),
                        amount=Decimal("1"),
                        unit=DoseUnit.MG,
                        route=Route.ORAL,
                    )
                )
            elif reference_kind == "report":
                session.add(
                    ReportSnapshot(
                        owner_id=owner_id,
                        date_from=date(2026, 1, 1),
                        date_to=date(2026, 1, 1),
                        timezone="UTC",
                        selected_sections=["approved_plan"],
                        include_ai=False,
                        source_manifest={
                            "fact": [],
                            "plan": [target_id],
                            "patient_note": [],
                            "ai": [],
                        },
                        metric_values={},
                        snapshot_content={"fact": [], "plan": [], "patient_note": [], "ai": []},
                        render_version="synthetic-v1",
                        canonical_sha256="a" * 64,
                    )
                )
            elif reference_kind == "analysis":
                session.add(
                    AIAnalysis(
                        owner_id=owner_id,
                        analysis_type=AnalysisType.PATTERN_OBSERVATION,
                        body="Synthetic analysis.",
                        source_record_ids=[target_id],
                        computed_inputs={},
                        model_name="synthetic-model",
                        model_digest="a" * 64,
                        prompt_version="synthetic-v1",
                    )
                )
            elif reference_kind == "draft":
                session.add(
                    ExtractionDraft(
                        owner_id=owner_id,
                        source="web",
                        candidates=[{"type": "dose", "medication_id": str(medication.id)}],
                        original_candidates=None,
                        state=DraftState.PENDING,
                        prompt_version="synthetic-v1",
                        schema_version="synthetic-v1",
                    )
                )
            else:
                regimen.source_document_checksum = "b" * 64
                session.add(
                    LabDocument(
                        owner_id=owner_id,
                        display_name="synthetic-source.pdf",
                        media_type="application/pdf",
                        sha256="b" * 64,
                        byte_size=1,
                        status=LabDocumentStatus.PENDING,
                    )
                )
            session.flush()
            session.expire_all()

            preview = preview_synthetic_bootstrap(session, owner_id=owner_id)
            assert preview.references.total > 0
            with pytest.raises(SyntheticBootstrapCleanupError, match="retained references"):
                execute_synthetic_bootstrap_cleanup(
                    session,
                    owner_id=owner_id,
                    preview=preview,
                    confirmation=preview.confirmation_phrase,
                )
            assert session.get(RegimenVersion, target["regimen_id"]) is not None
            assert all(
                session.get(Medication, row_id) is not None for row_id in target["medication_ids"]
            )
    finally:
        transaction.rollback()
        connection.close()


def test_synthetic_bootstrap_cleanup_refuses_near_match_and_ambiguous_targets(
    client: TestClient, engine: Engine
) -> None:
    _ = client
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection, expire_on_commit=False) as session:
            owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
            assert owner_id is not None
            target = _legacy_medication_bootstrap(session, owner_id=owner_id, approved=False)
            instruction = session.get(ApprovedInstruction, target["instruction_ids"][0])
            assert instruction is not None
            instruction.body = "Different synthetic placeholder content."
            session.flush()
            session.expire_all()

            with pytest.raises(SyntheticBootstrapCleanupError, match="no single exact"):
                preview_synthetic_bootstrap(session, owner_id=owner_id)
            assert session.get(RegimenVersion, target["regimen_id"]) is not None
    finally:
        transaction.rollback()
        connection.close()


def test_synthetic_bootstrap_cleanup_requires_exact_slot_medication_identity(
    client: TestClient, engine: Engine
) -> None:
    _ = client
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection, expire_on_commit=False) as session:
            owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
            assert owner_id is not None
            target = _legacy_medication_bootstrap(session, owner_id=owner_id, approved=False)
            lookalike = Medication(
                owner_id=owner_id,
                name="Hydrocortisone",
                normalized_name="hydrocortisone",
                formulation="tablet",
                strength=Decimal("20"),
                strength_unit="mg",
                default_unit=DoseUnit.MG,
                default_route=Route.ORAL,
            )
            session.add(lookalike)
            session.flush()
            slot = session.get(RegimenDoseSlot, target["slot_ids"][0])
            assert slot is not None
            slot.medication_id = lookalike.id
            session.flush()
            session.expire_all()

            with pytest.raises(SyntheticBootstrapCleanupError, match="no single exact"):
                preview_synthetic_bootstrap(session, owner_id=owner_id)
            assert session.get(RegimenVersion, target["regimen_id"]) is not None
    finally:
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# SAFE-16: approval is a human act with provenance
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-16")
def test_draft_plan_can_be_replaced_atomically_but_approved_plan_is_immutable(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    medication_id = _a_medication(client, logged_in)
    original = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            "version_label": "Synthetic editable draft",
            "effective_from": "2035-01-01T00:00:00",
            "slots": [],
            "instructions": [],
        },
    )
    assert original.status_code == 201, original.text
    version_id = original.json()["id"]
    replacement: dict[str, Any] = {
        "version_label": "Synthetic revised draft",
        "effective_from": "2035-02-01T00:00:00",
        "effective_to": "2035-03-01T00:00:00",
        "slots": [
            {
                "medication_id": medication_id,
                "scheduled_local_time": "08:30:00",
                "amount": "12.5",
                "unit": "mg",
                "route": "oral",
                "condition": "Synthetic condition",
                "sort_order": 0,
            }
        ],
        "instructions": [
            {
                "category": "general",
                "title": "Synthetic title",
                "body": "Synthetic physician text",
                "authored_by": "Dr Synthetic Private",
                "authored_on": "2035-01-15",
                "sort_order": 0,
            }
        ],
    }

    assert client.put(f"/api/v1/regimens/{version_id}", json=replacement).status_code == 403
    updated = client.put(f"/api/v1/regimens/{version_id}", json=replacement, headers=logged_in)
    assert updated.status_code == 200, updated.text
    assert updated.json()["status"] == "draft"
    assert updated.json()["version_label"] == "Synthetic revised draft"
    assert Decimal(updated.json()["slots"][0]["amount"]) == Decimal("12.5")
    assert updated.json()["instructions"][0]["title"] == "Synthetic title"

    impossible = {**replacement, "version_label": "Must not partly apply"}
    impossible["slots"] = [{**replacement["slots"][0], "medication_id": str(uuid.uuid4())}]
    refused = client.put(f"/api/v1/regimens/{version_id}", json=impossible, headers=logged_in)
    assert refused.status_code == 422
    unchanged = next(
        item for item in client.get("/api/v1/regimens").json()["items"] if item["id"] == version_id
    )
    assert unchanged["version_label"] == "Synthetic revised draft"
    assert Decimal(unchanged["slots"][0]["amount"]) == Decimal("12.5")

    approval = client.post(
        f"/api/v1/regimens/{version_id}/approve",
        headers=logged_in,
        json={
            "approved_by": "Dr Synthetic Private",
            "approval_source": "Synthetic private portal message",
        },
    )
    assert approval.status_code == 200, approval.text
    immutable = client.put(f"/api/v1/regimens/{version_id}", json=replacement, headers=logged_in)
    assert immutable.status_code == 409

    with Session(engine) as session:
        entries = list(
            session.scalars(
                select(AuditEntry).where(
                    AuditEntry.target_id == uuid.UUID(version_id),
                    AuditEntry.action.in_(
                        [AuditAction.REGIMEN_DRAFT_UPDATED, AuditAction.REGIMEN_APPROVED]
                    ),
                )
            )
        )
        assert {entry.action for entry in entries} == {
            AuditAction.REGIMEN_DRAFT_UPDATED,
            AuditAction.REGIMEN_APPROVED,
        }
        summaries = " ".join(entry.change_summary or "" for entry in entries)
        assert "Synthetic" not in summaries
        assert "12.5" not in summaries


@pytest.mark.safety("SAFE-13", "SAFE-16")
def test_draft_dates_are_optional_and_activation_hands_off_atomically(engine: Engine) -> None:
    activation_at = datetime(2042, 6, 1, 13, 45, tzinfo=UTC)
    with Session(engine, expire_on_commit=False) as session, session.begin():
        owner = Owner(
            email=f"optional-plan-{uuid.uuid4()}@example.test",
            password_hash="synthetic-non-login-hash",
            default_timezone="America/New_York",
        )
        session.add(owner)
        session.flush()
        predecessor = medication_service.create_draft(
            session,
            owner_id=owner.id,
            version_label="Synthetic predecessor",
            effective_from=datetime(2042, 1, 1),  # noqa: DTZ001
            effective_timezone="America/New_York",
        )
        medication_service.approve_version(
            session,
            predecessor,
            approved_by="Dr Synthetic",
            approval_source="synthetic fixture",
            activation_at=datetime(2042, 1, 1, tzinfo=UTC),
        )
        successor = medication_service.create_draft(
            session,
            owner_id=owner.id,
            version_label="Synthetic start-on-activation draft",
            effective_timezone="America/New_York",
        )
        assert successor.effective_from is None
        assert successor.effective_to is None
        assert successor.effective_time_provenance == "pending_activation"

        activation = medication_service.activate_version(
            session,
            successor,
            approved_by="Dr Synthetic",
            approval_source="synthetic fixture",
            activation_at=activation_at,
        )

        expected = activation_at.replace(tzinfo=None)
        assert activation.predecessor is predecessor
        assert successor.effective_from == expected
        assert successor.effective_from_local == datetime(2042, 6, 1, 9, 45)  # noqa: DTZ001
        assert successor.effective_from_utc_offset_minutes == -240
        assert successor.effective_time_provenance == "activation_instant"
        assert successor.effective_to is None
        assert predecessor.effective_to == expected
        assert predecessor.effective_period.upper == expected
        assert successor.effective_period.lower == expected


@pytest.mark.safety("SAFE-16")
def test_unsafe_retroactive_activation_is_clear_and_non_mutating(engine: Engine) -> None:
    with Session(engine, expire_on_commit=False) as session, session.begin():
        owner = Owner(
            email=f"retroactive-plan-{uuid.uuid4()}@example.test",
            password_hash="synthetic-non-login-hash",
            default_timezone="UTC",
        )
        session.add(owner)
        session.flush()
        approved = medication_service.create_draft(
            session,
            owner_id=owner.id,
            version_label="Synthetic future approved history",
            effective_from=datetime(2044, 2, 1),  # noqa: DTZ001
        )
        medication_service.approve_version(
            session,
            approved,
            approved_by="Dr Synthetic",
            approval_source="synthetic fixture",
        )
        draft = medication_service.create_draft(
            session,
            owner_id=owner.id,
            version_label="Synthetic unsafe retroactive draft",
            effective_from=datetime(2044, 1, 1),  # noqa: DTZ001
        )

        with pytest.raises(medication_service.PlanError, match="conflicts with"):
            medication_service.activate_version(
                session,
                draft,
                approved_by="Dr Synthetic",
                approval_source="synthetic fixture",
            )

        assert draft.status.value == "draft"
        assert draft.approved_at is None
        assert draft.effective_from == datetime(2044, 1, 1)  # noqa: DTZ001
        assert approved.effective_to is None


@pytest.mark.safety("SAFE-16")
def test_concurrent_start_on_activation_allows_only_one_plan(engine: Engine) -> None:
    activation_at = datetime(2046, 5, 1, 12, tzinfo=UTC)
    with Session(engine) as session, session.begin():
        owner = Owner(
            email=f"concurrent-plan-{uuid.uuid4()}@example.test",
            password_hash="synthetic-non-login-hash",
            default_timezone="UTC",
        )
        session.add(owner)
        session.flush()
        owner_id = owner.id
        draft_ids = [
            medication_service.create_draft(
                session,
                owner_id=owner.id,
                version_label=f"Synthetic concurrent draft {index}",
            ).id
            for index in range(2)
        ]

    barrier = Barrier(2)

    def activate(version_id: uuid.UUID) -> str:
        with Session(engine) as session, session.begin():
            draft = session.get(RegimenVersion, version_id)
            assert draft is not None
            barrier.wait(timeout=10)
            try:
                medication_service.activate_version(
                    session,
                    draft,
                    approved_by="Dr Synthetic",
                    approval_source="synthetic fixture",
                    activation_at=activation_at,
                )
            except medication_service.PlanError:
                return "conflict"
            return "approved"

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(activate, draft_ids))

    assert sorted(results) == ["approved", "conflict"]
    with Session(engine) as session:
        approved_count = session.scalar(
            select(func.count())
            .select_from(RegimenVersion)
            .where(
                RegimenVersion.owner_id == owner_id,
                RegimenVersion.status == "approved",
            )
        )
        assert approved_count == 1


@pytest.mark.safety("SAFE-16")
def test_regimen_lifecycle_normalizes_aware_effective_dates_in_one_session(
    engine: Engine,
) -> None:
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection, expire_on_commit=False) as session:
            owner = Owner(
                email=f"aware-plan-{uuid.uuid4()}@example.test",
                password_hash="synthetic-non-login-hash",
                default_timezone="UTC",
            )
            session.add(owner)
            session.flush()
            draft = medication_service.create_draft(
                session,
                owner_id=owner.id,
                version_label="Synthetic aware plan",
                effective_from=datetime.fromisoformat("2020-01-01T05:00:00+05:00"),
                effective_to=datetime.fromisoformat("2030-01-01T05:00:00+05:00"),
            )
            assert draft.effective_from == datetime(2020, 1, 1, tzinfo=UTC).replace(tzinfo=None)
            assert draft.effective_to == datetime(2030, 1, 1, tzinfo=UTC).replace(tzinfo=None)
            assert draft.effective_period.lower == draft.effective_from
            assert draft.effective_period.upper == draft.effective_to

            naive_from = datetime(2020, 2, 1, tzinfo=UTC).replace(tzinfo=None)
            naive_to = datetime(2030, 2, 1, tzinfo=UTC).replace(tzinfo=None)
            medication_service.update_draft(
                session,
                draft,
                version_label="Synthetic naive plan",
                effective_from=naive_from,
                effective_to=naive_to,
            )
            assert draft.effective_from == naive_from
            assert draft.effective_to == naive_to

            medication_service.update_draft(
                session,
                draft,
                version_label="Synthetic aware plan updated",
                effective_from=datetime.fromisoformat("2020-03-01T00:00:00-04:00"),
                effective_to=datetime.fromisoformat("2030-03-01T00:00:00-04:00"),
            )
            medication_service.approve_version(
                session,
                draft,
                approved_by="Dr Synthetic",
                approval_source="synthetic fixture",
            )
            medication_service.retire_version(
                session, draft, retired_at=datetime(2022, 1, 1, tzinfo=UTC)
            )
            session.flush()
            assert draft.effective_from == datetime(2020, 3, 1, 4, tzinfo=UTC).replace(tzinfo=None)
            assert draft.effective_to == datetime(2022, 1, 1, tzinfo=UTC).replace(tzinfo=None)
            assert draft.effective_period.upper == draft.effective_to
    finally:
        transaction.rollback()
        connection.close()


@pytest.mark.safety("SAFE-16")
def test_api_normalizes_aware_regimen_dates_through_approval_and_retirement(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            "version_label": "Synthetic aware API plan",
            "effective_from": "1970-01-01T05:00:00+05:00",
            "effective_to": "1971-01-01T05:00:00+05:00",
            "slots": [],
            "instructions": [],
        },
    )
    assert created.status_code == 201, created.text
    version_id = created.json()["id"]
    assert created.json()["effective_from"] == "1970-01-01T00:00:00"
    assert created.json()["effective_to"] == "1971-01-01T00:00:00"

    updated = client.put(
        f"/api/v1/regimens/{version_id}",
        headers=logged_in,
        json={
            "version_label": "Synthetic aware API plan updated",
            "effective_from": "1970-03-01T00:00:00-05:00",
            "effective_to": "1971-03-01T00:00:00-05:00",
            "slots": [],
            "instructions": [],
        },
    )
    assert updated.status_code == 200, updated.text
    assert updated.json()["effective_from"] == "1970-03-01T05:00:00"
    assert updated.json()["effective_to"] == "1971-03-01T05:00:00"

    approval = client.post(
        f"/api/v1/regimens/{version_id}/approve",
        headers=logged_in,
        json={"approved_by": "Dr Synthetic", "approval_source": "synthetic fixture"},
    )
    assert approval.status_code == 200, approval.text
    retired = client.post(f"/api/v1/regimens/{version_id}/retire", headers=logged_in)
    assert retired.status_code == 200, retired.text
    assert retired.json()["status"] == "retired"
    assert retired.json()["effective_from"] == "1970-03-01T05:00:00"
    assert retired.json()["effective_to"] == "1971-03-01T05:00:00"


@pytest.mark.safety("SAFE-13", "SAFE-16")
def test_regimen_effective_times_preserve_local_timezone_and_reject_dst_guessing(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    payload = {
        "version_label": "Synthetic zoned plan",
        "effective_from": "2026-08-12T09:30:00",
        "effective_to": "2026-08-13T09:30:00",
        "effective_timezone": "America/New_York",
        "slots": [],
        "instructions": [],
    }
    created = client.post("/api/v1/regimens", headers=logged_in, json=payload)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["effective_from"] == "2026-08-12T13:30:00"
    assert body["effective_from_local"] == "2026-08-12T09:30:00"
    assert body["effective_timezone"] == "America/New_York"
    assert body["effective_from_utc_offset_minutes"] == -240
    assert body["effective_time_provenance"] == "explicit_timezone"

    owner_default = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            **payload,
            "version_label": "Synthetic owner-timezone plan",
            "effective_timezone": None,
        },
    )
    assert owner_default.status_code == 201, owner_default.text
    assert owner_default.json()["effective_timezone"] == "Europe/London"
    assert owner_default.json()["effective_from"] == "2026-08-12T08:30:00"
    assert owner_default.json()["effective_from_utc_offset_minutes"] == 60

    invalid_timezone = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            **payload,
            "version_label": "Synthetic invalid-timezone plan",
            "effective_timezone": "Mars/Olympus",
        },
    )
    assert invalid_timezone.status_code == 422
    assert "unknown IANA timezone" in invalid_timezone.json()["detail"]

    nonexistent = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            **payload,
            "version_label": "Synthetic skipped plan",
            "effective_from": "2026-03-08T02:30:00",
            "effective_to": None,
        },
    )
    assert nonexistent.status_code == 422
    assert "does not exist" in nonexistent.json()["detail"]

    ambiguous = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            **payload,
            "version_label": "Synthetic repeated plan",
            "effective_from": "2026-11-01T01:30:00",
            "effective_to": None,
        },
    )
    assert ambiguous.status_code == 422
    assert "occurs twice" in ambiguous.json()["detail"]

    selected_fold = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            **payload,
            "version_label": "Synthetic selected repeated plan",
            "effective_from": "2026-11-01T01:30:00",
            "effective_to": None,
            "effective_from_fold": 1,
        },
    )
    assert selected_fold.status_code == 201, selected_fold.text
    assert selected_fold.json()["effective_from"] == "2026-11-01T06:30:00"
    assert selected_fold.json()["effective_from_utc_offset_minutes"] == -300


@pytest.mark.safety("SAFE-16")
def test_draft_plan_update_hides_another_owners_version(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    with Session(engine) as session, session.begin():
        other = Owner(
            email="other-plan-editor@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(other)
        session.flush()
        draft = medication_service.create_draft(
            session,
            owner_id=other.id,
            version_label="Other owner draft",
            effective_from=datetime(2039, 1, 1, tzinfo=UTC),
        )
        version_id = draft.id

    response = client.put(
        f"/api/v1/regimens/{version_id}",
        headers=logged_in,
        json={
            "version_label": "Synthetic attempted edit",
            "effective_from": "2039-02-01T00:00:00",
            "slots": [],
            "instructions": [],
        },
    )
    assert response.status_code == 404


@pytest.mark.safety("SAFE-16")
def test_approval_requires_an_approver_and_a_source(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    version_id = _a_draft_regimen(client, logged_in)
    for payload in ({"approved_by": "Dr X"}, {"approval_source": "letter"}, {}):
        response = client.post(
            f"/api/v1/regimens/{version_id}/approve", json=payload, headers=logged_in
        )
        assert response.status_code == 422, payload


@pytest.mark.safety("SAFE-13", "SAFE-16")
def test_api_sets_undated_draft_live_and_audits_predecessor_handoff(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    del logged_in
    email = f"api-handoff-{uuid.uuid4()}@example.com"
    with Session(engine) as session, session.begin():
        session.add(
            Owner(
                email=email,
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="Europe/London",
            )
        )
    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    headers = {auth.CSRF_HEADER_NAME: login.json()["csrf_token"]}
    predecessor_created = client.post(
        "/api/v1/regimens",
        headers=headers,
        json={
            "version_label": "Synthetic current predecessor",
            "effective_from": "2020-01-01T00:00:00",
            "effective_timezone": "Europe/London",
            "slots": [],
            "instructions": [],
        },
    )
    assert predecessor_created.status_code == 201, predecessor_created.text
    predecessor_id = predecessor_created.json()["id"]
    predecessor = client.post(
        f"/api/v1/regimens/{predecessor_id}/approve",
        headers=headers,
        json={"approved_by": "Dr Synthetic", "approval_source": "synthetic fixture"},
    )
    assert predecessor.status_code == 200, predecessor.text

    created = client.post(
        "/api/v1/regimens",
        headers=headers,
        json={
            "version_label": "Synthetic undated successor",
            "effective_from": None,
            "effective_to": None,
            "effective_timezone": "Europe/London",
            "slots": [],
            "instructions": [],
        },
    )
    assert created.status_code == 201, created.text
    assert created.json()["effective_from"] is None
    assert created.json()["effective_time_provenance"] == "pending_activation"

    activated = client.post(
        f"/api/v1/regimens/{created.json()['id']}/approve",
        headers=headers,
        json={
            "approved_by": "Dr Synthetic",
            "approval_source": "synthetic fixture",
            "activation_local_time": "2030-06-01T00:00:00",
            "activation_timezone": "Europe/London",
        },
    )
    assert activated.status_code == 200, activated.text
    body = activated.json()
    assert body["status"] == "approved"
    assert body["effective_from"] == "2030-05-31T23:00:00"
    assert body["effective_from_local"] == "2030-06-01T00:00:00"
    assert body["effective_time_provenance"] == "explicit_timezone"
    assert body["effective_to"] is None

    history = client.get("/api/v1/regimens").json()["items"]
    ended = next(item for item in history if item["id"] == predecessor_id)
    assert ended["effective_to"] == body["effective_from"]
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(
                    AuditEntry.target_id == uuid.UUID(predecessor_id),
                    AuditEntry.action == AuditAction.REGIMEN_HANDOFF,
                )
            )
            == 1
        )


@pytest.mark.safety("SAFE-16")
def test_a_draft_is_not_the_active_plan(client: TestClient, logged_in: dict[str, str]) -> None:
    """An unapproved schedule must never be treated as the plan in force."""
    _a_draft_regimen(client, logged_in)
    assert client.get("/api/v1/regimens/active").json() is None


@pytest.mark.safety("SAFE-16")
def test_approved_version_cannot_be_approved_again(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    version_id = _a_draft_regimen(client, logged_in)
    approval = {"approved_by": "Dr Example", "approval_source": "clinic letter"}
    first = client.post(f"/api/v1/regimens/{version_id}/approve", json=approval, headers=logged_in)
    assert first.status_code == 200, first.text
    assert first.json()["approved_by"] == "Dr Example"

    second = client.post(f"/api/v1/regimens/{version_id}/approve", json=approval, headers=logged_in)
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# SAFE-13: ambiguity is surfaced
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-13")
def test_ambiguous_dst_time_is_rejected_not_guessed(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    medication_id = _a_medication(client, logged_in)
    response = client.post(
        "/api/v1/doses",
        json={
            "medication_id": medication_id,
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            # 01:30 happens twice in London on this date.
            "time": {"local_time": "2026-10-25T01:30:00", "timezone": "Europe/London"},
        },
        headers=logged_in,
    )
    assert response.status_code == 422
    assert "occurs twice" in response.json()["detail"]


@pytest.mark.safety("SAFE-13")
def test_nonexistent_dst_time_is_rejected(client: TestClient, logged_in: dict[str, str]) -> None:
    medication_id = _a_medication(client, logged_in)
    response = client.post(
        "/api/v1/doses",
        json={
            "medication_id": medication_id,
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": "2026-03-29T01:30:00", "timezone": "Europe/London"},
        },
        headers=logged_in,
    )
    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


# ---------------------------------------------------------------------------
# SAFE-07: exports keep the partition, AI off by default
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-07")
def test_export_separates_categories_and_excludes_ai_by_default(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    _, _, downloaded = _complete_private_export(
        client, logged_in, engine, settings, key="category-export"
    )
    payload = downloaded.json()
    assert set(payload) >= {"plan", "facts", "ai"}
    assert payload["ai"] == {}, "AI content must be excluded unless asked for"
    assert "credentials" in payload["notice"]


@pytest.mark.safety("SAFE-07")
def test_report_snapshot_generation_companions_immutable_retrieval_and_audit(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    note_text = "Synthetic physician question for immutable report"
    medication_id = _a_medication(client, logged_in)
    regimen = client.post(
        "/api/v1/regimens",
        headers=logged_in,
        json={
            "version_label": "Synthetic report plan",
            "effective_from": "2025-08-09T00:00:00",
            "effective_to": "2025-08-10T00:00:00",
            "slots": [
                {
                    "medication_id": medication_id,
                    "scheduled_local_time": "07:00:00",
                    "amount": "10",
                    "unit": "mg",
                    "route": "oral",
                }
            ],
            "instructions": [],
        },
    )
    assert regimen.status_code == 201, regimen.text
    approved = client.post(
        f"/api/v1/regimens/{regimen.json()['id']}/approve",
        headers=logged_in,
        json={"approved_by": "Dr Synthetic", "approval_source": "Synthetic fixture"},
    )
    assert approved.status_code == 200, approved.text
    dose = client.post(
        "/api/v1/doses",
        headers=logged_in,
        json={
            "medication_id": medication_id,
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": "2025-08-09T07:05:00", "timezone": "Europe/London"},
        },
    )
    assert dose.status_code == 201, dose.text
    note = client.post(
        "/api/v1/diary-events",
        headers=logged_in,
        json={
            "text": note_text,
            "time": {"local_time": "2025-08-09T12:00:00", "timezone": "Europe/London"},
        },
    )
    assert note.status_code == 201, note.text
    note_id = note.json()["id"]
    blood_pressure = client.post(
        "/api/v1/blood-pressure",
        headers=logged_in,
        json={
            "systolic_mmhg": 118,
            "diastolic_mmhg": 76,
            "pulse_bpm": 62,
            "measurement_setting": "provider",
            "time": {"local_time": "2025-08-09T08:15:00", "timezone": "Europe/London"},
        },
    )
    assert blood_pressure.status_code == 201, blood_pressure.text
    weight = client.post(
        "/api/v1/weight",
        headers=logged_in,
        json={
            "value": "180",
            "unit": "lb",
            "time": {"local_time": "2025-08-09T08:20:00", "timezone": "Europe/London"},
        },
    )
    assert weight.status_code == 201, weight.text
    temperature = client.post(
        "/api/v1/temperature",
        headers=logged_in,
        json={
            "value": "38",
            "unit": "c",
            "time": {"local_time": "2025-08-09T08:25:00", "timezone": "Europe/London"},
        },
    )
    assert temperature.status_code == 201, temperature.text
    request = {
        "date_from": "2025-08-09",
        "date_to": "2025-08-09",
        "timezone": "Europe/London",
        "selected_sections": ["metrics", "doses", "vitals", "approved_plan", "patient_notes"],
        "companion_formats": ["csv", "json"],
    }
    assert client.post("/api/v1/reports", json=request).status_code == 403
    generated = client.post("/api/v1/reports", headers=logged_in, json=request)
    assert generated.status_code == 201, generated.text
    body = generated.json()
    assert body["include_ai"] is False
    assert {artifact["format"] for artifact in body["artifacts"]} == {"pdf", "csv", "json"}
    report_id = body["id"]

    preview = client.get(f"/api/v1/reports/{report_id}")
    assert preview.status_code == 200
    frozen = preview.json()
    assert frozen["canonical_sha256"] == body["canonical_sha256"]
    assert frozen["snapshot_content"]["ai"] == []
    assert frozen["source_manifest"]["ai"] == []
    assert frozen["snapshot_content"]["patient_note"][0]["text"] == note_text
    facts_by_type = {record["record_type"]: record for record in frozen["snapshot_content"]["fact"]}
    assert facts_by_type["dose"]["amount"] == "10.0000"
    assert facts_by_type["blood_pressure"]["systolic_mmhg"] == 118
    assert facts_by_type["blood_pressure"]["diastolic_mmhg"] == 76
    assert facts_by_type["blood_pressure"]["pulse_bpm"] == 62
    assert facts_by_type["blood_pressure"]["measurement_setting"] == "provider"
    assert facts_by_type["weight"]["value"] == "180.0000"
    assert facts_by_type["weight"]["unit"] == "lb"
    assert facts_by_type["weight"]["normalized_kg"] == "81.6466"
    assert facts_by_type["weight"]["display_lb"] == "180.0"
    assert facts_by_type["weight"]["measurement_setting"] == "home"
    assert facts_by_type["weight"]["normalization_definition"] == "1 lb = 0.45359237 kg"
    assert facts_by_type["temperature"]["value"] == "38.00"
    assert facts_by_type["temperature"]["unit"] == "c"
    assert facts_by_type["temperature"]["display_f"] == "100.4"
    assert facts_by_type["temperature"]["display_c"] == "38.0"
    assert frozen["snapshot_content"]["plan"][0]["record_type"] == "approved_regimen"
    assert dose.json()["id"] in frozen["source_manifest"]["fact"]
    assert blood_pressure.json()["id"] in frozen["source_manifest"]["fact"]
    assert weight.json()["id"] in frozen["source_manifest"]["fact"]
    assert temperature.json()["id"] in frozen["source_manifest"]["fact"]
    assert regimen.json()["id"] in frozen["source_manifest"]["plan"]
    assert all(
        metric["definition"] and metric["timezone"] for metric in frozen["metric_values"].values()
    )

    for format_name, content_type in (
        ("pdf", "application/pdf"),
        ("csv", "text/csv"),
        ("json", "application/json"),
    ):
        downloaded = client.get(f"/api/v1/reports/{report_id}/artifacts/{format_name}")
        assert downloaded.status_code == 200
        assert downloaded.headers["content-type"].startswith(content_type)
        assert "attachment" in downloaded.headers["content-disposition"]
        assert downloaded.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/reports/{report_id}/artifacts/pdf").content.startswith(b"%PDF-")

    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        session.add(
            AIAnalysis(
                owner_id=owner_id,
                analysis_type=AnalysisType.REPORT_NARRATIVE,
                body="Synthetic optional AI narrative",
                source_record_ids=[dose.json()["id"]],
                range_start=datetime(2025, 8, 9, tzinfo=UTC),
                range_end=datetime(2025, 8, 10, tzinfo=UTC),
                computed_inputs={"dose_total": "10.0000"},
                model_name="synthetic-model",
                model_digest="a" * 64,
                prompt_version="synthetic-v1",
            )
        )
    opted_in = client.post(
        "/api/v1/reports",
        headers=logged_in,
        json={**request, "include_ai": True, "companion_formats": []},
    )
    assert opted_in.status_code == 201, opted_in.text
    opted_preview = client.get(f"/api/v1/reports/{opted_in.json()['id']}").json()
    assert opted_preview["include_ai"] is True
    assert opted_preview["snapshot_content"]["ai"][0]["body"] == "Synthetic optional AI narrative"

    deleted = client.request(
        "DELETE",
        f"/api/v1/privacy/records/diary/{note_id}",
        headers=logged_in,
        json={"password": PASSWORD},
    )
    assert deleted.status_code == 204, deleted.text
    assert (
        client.get(f"/api/v1/reports/{report_id}").json()["snapshot_content"]["patient_note"][0][
            "text"
        ]
        == note_text
    )

    with Session(engine) as session:
        report_uuid = uuid.UUID(report_id)
        assert session.scalar(select(ReportSnapshot).where(ReportSnapshot.id == report_uuid))
        assert (
            len(
                list(
                    session.scalars(
                        select(ReportArtifact).where(ReportArtifact.snapshot_id == report_uuid)
                    )
                )
            )
            == 3
        )
        entries = list(
            session.scalars(
                select(AuditEntry).where(
                    AuditEntry.target_id == report_uuid,
                    AuditEntry.action.in_(
                        (AuditAction.REPORT_GENERATED, AuditAction.REPORT_DOWNLOADED)
                    ),
                )
            )
        )
        assert {entry.action for entry in entries} == {
            AuditAction.REPORT_GENERATED,
            AuditAction.REPORT_DOWNLOADED,
        }
        assert all(note_text not in (entry.change_summary or "") for entry in entries)
    assert any(settings.report_artifacts_dir.rglob(f"{report_id}/report.pdf"))


def test_report_validation_and_owner_boundary(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    base = {
        "date_from": "2026-08-09",
        "date_to": "2026-08-09",
        "selected_sections": ["metrics"],
    }
    duplicate = client.post(
        "/api/v1/reports",
        headers=logged_in,
        json={**base, "selected_sections": ["metrics", "metrics"]},
    )
    assert duplicate.status_code == 422
    unsupported = client.post(
        "/api/v1/reports",
        headers=logged_in,
        json={**base, "selected_sections": ["medical_recommendations"]},
    )
    assert unsupported.status_code == 422
    too_long = client.post(
        "/api/v1/reports",
        headers=logged_in,
        json={**base, "date_to": "2028-08-09"},
    )
    assert too_long.status_code == 422

    with Session(engine) as session, session.begin():
        other = Owner(
            email="report-boundary@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(other)
        session.flush()
        hidden = ReportSnapshot(
            owner_id=other.id,
            date_from=datetime(2026, 8, 9, tzinfo=UTC).date(),
            date_to=datetime(2026, 8, 9, tzinfo=UTC).date(),
            timezone="UTC",
            selected_sections=["metrics"],
            include_ai=False,
            source_manifest={"fact": [], "plan": [], "patient_note": [], "ai": []},
            metric_values={},
            snapshot_content={"fact": [], "plan": [], "patient_note": [], "ai": []},
            render_version="report-v1",
            canonical_sha256="0" * 64,
        )
        session.add(hidden)
        session.flush()
        hidden_id = hidden.id
    assert client.get(f"/api/v1/reports/{hidden_id}").status_code == 404


# ---------------------------------------------------------------------------
# SAFE-10 / SAFE-27: comparison derives, and states its definition
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-10")
def test_missing_doses_are_derived_not_stored(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    before = len(client.get("/api/v1/doses").json()["items"])
    comparison = client.get(
        "/api/v1/doses/plan-comparison",
        params={"day": "2026-05-02", "timezone": "Europe/London"},
    )
    assert comparison.status_code == 200, comparison.text
    after = len(client.get("/api/v1/doses").json()["items"])
    assert before == after, "comparing a day must not create dose rows"


@pytest.mark.parametrize(
    "path",
    [
        "/api/v1/diary-events",
        "/api/v1/life-events",
        "/api/v1/stress-episodes",
        "/api/v1/emergency-injections",
        "/api/v1/regimens",
        "/api/v1/reports",
        "/api/v1/labs/results",
        "/api/v1/labs/documents",
        "/api/v1/integrations/garmin/records",
        "/api/v1/data-quality",
    ],
)
def test_growing_history_endpoints_enforce_bounded_pages(client: TestClient, path: str) -> None:
    first = client.get(path, params={"page": 1, "page_size": 1})
    assert first.status_code == 200, first.text
    payload = first.json()
    assert payload["page"]["page"] == 1
    assert payload["page"]["page_size"] == 1
    items = payload.get("items", payload.get("records", payload.get("findings")))
    assert items is not None
    assert len(items) <= 1

    last_page = payload["page"]["total_pages"]
    last = client.get(path, params={"page": last_page, "page_size": 1})
    assert last.status_code == 200, last.text
    assert last.json()["page"]["page"] == last_page
    assert client.get(path, params={"page": last_page + 1, "page_size": 1}).status_code == 422


@pytest.mark.safety("SAFE-27")
def test_comparison_states_its_metric_definition_and_timezone(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    body = client.get(
        "/api/v1/doses/plan-comparison",
        params={"day": "2026-05-01", "timezone": "Europe/London"},
    ).json()
    assert body["timezone"] == "Europe/London"
    assert "30 minutes" in body["metric_definition"]
    assert "never stored as a zero dose" in body["metric_definition"]


def test_historical_plan_intervals_midday_dst_deviation_and_correction(
    engine: Engine,
) -> None:
    """Retired history and a DST-day transition remain the comparison authority."""
    connection = engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection, expire_on_commit=False) as session:
            owner = Owner(
                email="historical-plan-owner@example.test",
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="America/New_York",
            )
            session.add(owner)
            session.flush()
            medication = Medication(
                owner_id=owner.id,
                name="Synthetic replacement",
                normalized_name="synthetic replacement historical plan",
                default_unit=DoseUnit.MG,
                default_route=Route.ORAL,
            )
            session.add(medication)
            session.flush()

            unapproved = medication_service.create_draft(
                session,
                owner_id=owner.id,
                version_label="Never approved draft",
                effective_from=datetime(2026, 2, 1, tzinfo=UTC).replace(tzinfo=None),
                effective_to=datetime(2026, 4, 1, tzinfo=UTC).replace(tzinfo=None),
            )
            medication_service.retire_version(
                session, unapproved, retired_at=datetime(2026, 2, 20, tzinfo=UTC)
            )
            assert unapproved.effective_to == datetime(2026, 2, 20, tzinfo=UTC).replace(tzinfo=None)
            assert (
                medication_service.active_version_at(
                    session, owner.id, datetime(2026, 2, 10, tzinfo=UTC)
                )
                is None
            )

            transition = datetime(2026, 3, 8, 16, tzinfo=UTC).replace(
                tzinfo=None
            )  # noon after New York's DST jump
            earlier = medication_service.create_draft(
                session,
                owner_id=owner.id,
                version_label="Earlier weekly plan",
                effective_from=datetime(2026, 3, 1, 5, tzinfo=UTC).replace(tzinfo=None),
                effective_to=transition,
            )
            for clock in (time(8), time(12), time(18)):
                earlier.slots.append(
                    RegimenDoseSlot(
                        medication_id=medication.id,
                        scheduled_local_time=clock,
                        amount=Decimal("5"),
                        unit=DoseUnit.MG,
                        route=Route.ORAL,
                    )
                )
            medication_service.approve_version(
                session,
                earlier,
                approved_by="Dr Synthetic",
                approval_source="synthetic fixture",
            )
            medication_service.retire_version(
                session, earlier, retired_at=datetime(2026, 3, 8, 16, tzinfo=UTC)
            )

            later = medication_service.create_draft(
                session,
                owner_id=owner.id,
                version_label="Later weekly plan",
                effective_from=transition,
            )
            for clock in (time(12), time(16), time(20)):
                later.slots.append(
                    RegimenDoseSlot(
                        medication_id=medication.id,
                        scheduled_local_time=clock,
                        amount=Decimal("5"),
                        unit=DoseUnit.MG,
                        route=Route.ORAL,
                    )
                )
            medication_service.approve_version(
                session,
                later,
                approved_by="Dr Synthetic",
                approval_source="synthetic fixture",
            )
            session.flush()

            def record(day_value: date, clock: time) -> DoseEvent:
                event_time = resolve_event_time(
                    datetime.combine(day_value, clock), "America/New_York"
                )
                version, slot = medication_service.association_for_event_time(
                    session,
                    owner_id=owner.id,
                    medication_id=medication.id,
                    occurred_at=event_time.occurred_at,
                    local_time=event_time.local_time,
                    timezone=event_time.timezone,
                )
                return events.create_event(
                    session,
                    DoseEvent,
                    owner_id=owner.id,
                    event_time=event_time,
                    source_type=SourceType.WEB,
                    confirmation_state=ConfirmationState.DIRECT,
                    medication_id=medication.id,
                    amount=Decimal("5"),
                    unit=DoseUnit.MG,
                    route=Route.ORAL,
                    category=DoseCategory.SCHEDULED,
                    regimen_version_id=version.id if version else None,
                    slot_id=slot.id if slot else None,
                    episode_id=None,
                    notes=None,
                )

            for clock in (time(8, 20), time(12, 30), time(20, 10), time(23)):
                record(date(2026, 3, 8), clock)

            retired_lookup = medication_service.active_version_at(
                session, owner.id, datetime(2026, 3, 7, 13, tzinfo=UTC)
            )
            boundary_lookup = medication_service.active_version_at(
                session, owner.id, datetime(2026, 3, 8, 16, tzinfo=UTC)
            )
            assert retired_lookup is not None and retired_lookup.id == earlier.id
            assert boundary_lookup is not None and boundary_lookup.id == later.id

            comparison = medication_service.compare_day(
                session,
                owner_id=owner.id,
                day=date(2026, 3, 8),
                timezone="America/New_York",
            )
            rows = comparison["slots"]
            assert isinstance(rows, list)
            scheduled = [row for row in rows if row.scheduled_local_time is not None]
            assert [(row.scheduled_local_time, row.regimen_version_id) for row in scheduled] == [
                (time(8), earlier.id),
                (time(12), later.id),
                (time(16), later.id),
                (time(20), later.id),
            ]
            assert [row.status for row in rows] == [
                "on_time",
                "on_time",
                "missing",
                "on_time",
                "unplanned",
            ]
            assert [row.minutes_from_scheduled for row in rows] == [20, 30, None, 10, None]
            assert [row.absolute_minutes_from_scheduled for row in rows] == [
                20,
                30,
                None,
                10,
                None,
            ]

            analytics_summary = analytics_service.summary_for_owner(
                session,
                owner_id=owner.id,
                date_from=date(2026, 3, 8),
                date_to=date(2026, 3, 8),
                timezone="America/New_York",
            )
            timing = analytics_summary["timing"]
            assert isinstance(timing, dict)
            assert timing["matched_count"] == 3
            assert timing["total_absolute_deviation_minutes"] == Decimal("60")
            assert timing["average_absolute_deviation_minutes"] == Decimal("20")
            periods = cast(list[dict[str, Any]], timing["plan_periods"])
            assert [period["regimen_version_label"] for period in periods] == [
                "Earlier weekly plan",
                "Later weekly plan",
            ]
            assert periods[1]["missing_count"] == 1
            assert periods[1]["unplanned"] == 1

            original = record(date(2026, 3, 7), time(8, 5))
            record(date(2026, 3, 9), time(12, 5))
            multi_period_summary = analytics_service.summary_for_owner(
                session,
                owner_id=owner.id,
                date_from=date(2026, 3, 7),
                date_to=date(2026, 3, 9),
                timezone="America/New_York",
            )
            multi_timing = multi_period_summary["timing"]
            assert isinstance(multi_timing, dict)
            multi_periods = cast(list[dict[str, Any]], multi_timing["plan_periods"])
            assert [period["regimen_version_label"] for period in multi_periods] == [
                "Earlier weekly plan",
                "Later weekly plan",
            ]
            report = report_builder.build_snapshot(
                session,
                owner_id=owner.id,
                date_from=date(2026, 3, 7),
                date_to=date(2026, 3, 9),
                timezone="America/New_York",
                selected_sections=["metrics"],
            )
            report_timing = cast(dict[str, Any], report.metric_values["timing"])
            report_periods = cast(list[dict[str, Any]], report_timing["plan_periods"])
            assert [period["regimen_version_label"] for period in report_periods] == [
                "Earlier weekly plan",
                "Later weekly plan",
            ]
            assert report_timing["average_absolute_deviation_minutes"] is not None

            summary = medication_service.compare_day(
                session,
                owner_id=owner.id,
                day=date(2026, 2, 1),
                timezone="America/New_York",
            )
            assert summary["slots"] == []
            assert summary["planned_total"] is None

            assert original.regimen_version_id == earlier.id
            assert original.slot_id is not None
            corrected_time = resolve_event_time(
                datetime(2026, 3, 8, 12, 5, tzinfo=UTC).replace(tzinfo=None),
                "America/New_York",
            )
            corrected_version, corrected_slot = medication_service.association_for_event_time(
                session,
                owner_id=owner.id,
                medication_id=medication.id,
                occurred_at=corrected_time.occurred_at,
                local_time=corrected_time.local_time,
                timezone=corrected_time.timezone,
            )
            assert corrected_version is not None and corrected_version.id == later.id
            assert corrected_slot is not None and corrected_slot.regimen_version_id == later.id
            correction = events.correct_event(
                session,
                DoseEvent,
                original,
                reason="synthetic time correction",
                changes={
                    "regimen_version_id": corrected_version.id,
                    "slot_id": corrected_slot.id,
                },
                event_time=corrected_time,
            )
            assert correction.regimen_version_id == later.id
            assert correction.slot_id == corrected_slot.id
    finally:
        transaction.rollback()
        connection.close()


@pytest.mark.safety("SAFE-27")
def test_analytics_states_definitions_timezone_and_missingness(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    response = client.get(
        "/api/v1/analytics/summary",
        params={
            "date_from": "2030-01-01",
            "date_to": "2030-01-02",
            "timezone": "Pacific/Kiritimati",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["timezone"] == "Pacific/Kiritimati"
    assert body["daily_doses"]["sample_count"] == 0
    assert body["daily_doses"]["missing_count"] == 2
    assert body["daily_doses"]["values"][0]["actual_total"] is None
    assert "No recorded doses is shown as missing" in body["daily_doses"]["definition"]
    assert "30 minutes" in body["timing"]["definition"]
    assert body["episodes"]["definition"]
    assert body["symptoms"]["definition"]
    for metric_name in ("daily_doses", "timing", "episodes", "symptoms"):
        assert body[metric_name]["timezone"] == "Pacific/Kiritimati"

    invalid = client.get(
        "/api/v1/analytics/summary",
        params={"date_from": "2030-01-02", "date_to": "2030-01-01", "timezone": "UTC"},
    )
    assert invalid.status_code == 422


@pytest.mark.safety("SAFE-27")
def test_steroid_exposure_uses_current_actual_doses_and_sums_close_records(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
) -> None:
    selected_day = "2024-02-01"
    medication = client.post(
        "/api/v1/medications",
        json={
            "name": "Hydrocortisone",
            "formulation": "tablet",
            "strength": "7.1234",
            "strength_unit": "mg",
            "default_unit": "mg",
            "default_route": "oral",
        },
        headers=logged_in,
    )
    assert medication.status_code == 201, medication.text
    medication_id = medication.json()["id"]

    def record(
        local_time: str,
        amount: str,
        route: str = "oral",
        category: str = "scheduled",
    ) -> dict[str, Any]:
        response = client.post(
            "/api/v1/doses",
            json={
                "medication_id": medication_id,
                "amount": amount,
                "unit": "mg",
                "route": route,
                "category": category,
                "time": {"local_time": local_time, "timezone": "UTC"},
                "notes": "SYNTHETIC_PRIVATE_NOTE_MUST_NOT_APPEAR",
            },
            headers=logged_in,
        )
        assert response.status_code == 201, response.text
        return cast(dict[str, Any], response.json())

    prior = record("2024-01-31T23:30:00", "4")
    original = record("2024-02-01T07:00:00", "10")
    close = record("2024-02-01T07:01:00", "5", category="stress")
    unsupported = record("2024-02-01T13:00:00", "3", "intramuscular")
    corrected = client.post(
        f"/api/v1/doses/{original['id']}/correct",
        json={
            "reason": "Synthetic amount correction",
            "changes": {"amount": "12"},
        },
        headers=logged_in,
    )
    assert corrected.status_code == 201, corrected.text

    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        other = Owner(
            email=f"curve-other-{uuid.uuid4()}@example.test",
            password_hash="synthetic-non-login-hash",
            default_timezone="UTC",
        )
        session.add(other)
        session.flush()
        other_medication = Medication(
            owner_id=other.id,
            name="Hydrocortisone",
            normalized_name="hydrocortisone",
            formulation="tablet",
            strength=Decimal("8.4321"),
            strength_unit="mg",
            default_unit=DoseUnit.MG,
            default_route=Route.ORAL,
        )
        session.add(other_medication)
        session.flush()
        other_dose = events.create_event(
            session,
            DoseEvent,
            owner_id=other.id,
            event_time=resolve_event_time(
                datetime(2024, 2, 1, 7, 0),  # noqa: DTZ001
                "UTC",
            ),
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            medication_id=other_medication.id,
            amount=Decimal("99"),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            category=DoseCategory.SCHEDULED,
        )
        other_dose_id = str(other_dose.id)

        sync_run = GarminSyncRun(
            owner_id=owner_id,
            requested_start_date=date(2024, 1, 31),
            requested_end_date=date(2024, 2, 2),
            timezone="UTC",
            origin=GarminSyncOrigin.MANUAL,
            status=GarminSyncStatus.COMPLETED,
            started_at=datetime(2024, 2, 3, 8, 0, tzinfo=UTC),
            finished_at=datetime(2024, 2, 3, 8, 1, tzinfo=UTC),
            counts={"sleep": 2},
            warning_codes=[],
            client_version="synthetic-test",
        )
        session.add(sync_run)
        session.flush()

        for provider_id, started_at, ended_at in (
            (
                "synthetic-morning-sleep",
                datetime(2024, 1, 31, 23, 0, tzinfo=UTC),
                datetime(2024, 2, 1, 6, 30, tzinfo=UTC),
            ),
            (
                "synthetic-evening-sleep",
                datetime(2024, 2, 1, 22, 30, tzinfo=UTC),
                datetime(2024, 2, 2, 6, 30, tzinfo=UTC),
            ),
        ):
            sleep = GarminSleepEvent(
                owner_id=owner_id,
                recorded_at=datetime(2024, 2, 3, 8, 1, tzinfo=UTC),
                source_type=SourceType.PROVIDER,
                provider_id=provider_id,
                source_revision="synthetic-v1",
                import_batch_id=None,
                confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
                supersedes_id=None,
                correction_reason=None,
                notes=None,
                garmin_import_batch_id=None,
                garmin_sync_run_id=sync_run.id,
                garmin_source_member=provider_id,
                garmin_manufacturer="Garmin",
                garmin_product_name="Synthetic Test Device",
                garmin_device_serial_hash=None,
                ended_at=ended_at,
                overall_sleep_score=80,
                stage_count=0,
                duration_seconds=int((ended_at - started_at).total_seconds()),
                garmin_duration_source="provider",
                awakenings=0,
            )
            sleep.apply_event_time(from_instant(started_at, "UTC"))
            session.add(sleep)

    response = client.get(
        "/api/v1/analytics/steroid-exposure",
        params={"day": selected_day, "timezone": "UTC"},
    )
    assert response.status_code == 200, response.text
    body = response.json()
    marker_ids = {row["dose_event_id"] for row in body["dose_markers"]}
    corrected_id = corrected.json()["id"]

    assert body["model"]["version"] == "hc-exposure-v1"
    assert body["series_unit"] == "REU"
    assert body["elapsed_hours"] == "24.0"
    assert "not a cortisol measurement or dosing guide" in body["safety_label"]
    assert body["supported_dose_count"] == 3
    assert body["excluded_dose_count"] == 1
    assert set(marker_ids) == {prior["id"], corrected_id, close["id"], unsupported["id"]}
    assert original["id"] not in marker_ids
    assert other_dose_id not in marker_ids
    assert body["dose_markers"][0]["carryover"] is True
    assert (
        next(row for row in body["dose_markers"] if row["dose_event_id"] == close["id"])["category"]
        == "stress"
    )
    assert (
        next(row for row in body["dose_markers"] if row["dose_event_id"] == unsupported["id"])[
            "exclusion_reason"
        ]
        == "unsupported_route"
    )
    peak_at = next(row for row in body["dose_markers"] if row["dose_event_id"] == corrected_id)[
        "modeled_peak_at"
    ]
    peak_sample = next(row for row in body["samples"] if row["occurred_at"] == peak_at)
    assert Decimal(peak_sample["theoretical_exposure_reu"]) > Decimal("12")
    assert Decimal(peak_sample["regular_exposure_reu"]) > Decimal("12")
    assert Decimal(peak_sample["stress_exposure_reu"]) > 0
    assert Decimal(peak_sample["theoretical_exposure_reu"]) == (
        Decimal(peak_sample["regular_exposure_reu"]) + Decimal(peak_sample["stress_exposure_reu"])
    )
    assert body["context_band"]["default_visible"] is False
    assert body["context_band"]["band"]["personalized"] is False
    assert len(body["samples"]) == len(body["context_band"]["samples"])
    assert {row["occurred_at"] for row in body["samples"]} == {
        row["occurred_at"] for row in body["context_band"]["samples"]
    }
    assert "SYNTHETIC_PRIVATE_NOTE_MUST_NOT_APPEAR" not in response.text

    invalid = client.get(
        "/api/v1/analytics/steroid-exposure",
        params={"day": selected_day, "timezone": "Not/A_Zone"},
    )
    assert invalid.status_code == 422

    physiological = client.get(
        "/api/v1/analytics/steroid-exposure",
        params={
            "day": selected_day,
            "timezone": "UTC",
            "model": "hc-physiology-v2",
        },
    )
    assert physiological.status_code == 200, physiological.text
    physiological_body = physiological.json()
    assert physiological_body["model"]["id"] == "hc-physiology-v2"
    assert physiological_body["model"]["revision"] == "hc-physiology-v2.0.0"
    assert physiological_body["series_unit"] == "nmol/L"
    assert physiological_body["source_revision_sha256"]
    assert physiological_body["context_band"]["default_visible"] is False
    assert physiological_body["context_band"]["band"]["personalized"] is False
    assert len(physiological_body["samples"]) == len(physiological_body["context_band"]["samples"])
    assert {row["occurred_at"] for row in physiological_body["samples"]} == {
        row["occurred_at"] for row in physiological_body["context_band"]["samples"]
    }
    assert other_dose_id not in {row["dose_event_id"] for row in physiological_body["dose_markers"]}
    assert "SYNTHETIC_PRIVATE_NOTE_MUST_NOT_APPEAR" not in physiological.text

    wake_free = client.get(
        "/api/v1/analytics/steroid-exposure",
        params={
            "day": selected_day,
            "timezone": "UTC",
            "model": "hc-wake-free-v3",
        },
    )
    assert wake_free.status_code == 200, wake_free.text
    wake_free_body = wake_free.json()
    assert wake_free_body["model"]["id"] == "hc-wake-free-v3"
    assert wake_free_body["model"]["revision"] == "hc-wake-free-v3.0.0"
    assert wake_free_body["model"]["parameters"]["population_default"] is True
    assert wake_free_body["series_kind"] == "modeled_serum_free_cortisol_scenario"
    assert wake_free_body["series_unit"] == "nmol/L"
    assert wake_free_body["context_band"]["default_visible"] is False
    assert wake_free_body["wake_reference"]["available"] is True
    assert wake_free_body["wake_reference"]["missing_inputs"] == []
    assert wake_free_body["wake_reference"]["assumptions"]["wake_at"] == ("2024-02-01T06:30:00Z")
    assert wake_free_body["wake_reference"]["assumptions"]["sleep_onset_at"] == (
        "2024-02-01T22:30:00Z"
    )
    assert wake_free_body["wake_reference"]["assumptions"]["observed_meals"] == {}
    coverage = wake_free_body["coverage_features"]
    assert coverage["available"] is True
    assert coverage["feature_id"] == "hc-wake-coverage-v1"
    assert coverage["feature_revision"] == "hc-wake-coverage-v1.1.0"
    assert coverage["day_state"] == "complete"
    assert coverage["missing_inputs"] == []
    assert len(coverage["source_revision_sha256"]) == 64
    assert Decimal(coverage["comparison_minutes"]) > 0
    assert Decimal(coverage["expected_pre_wake_excluded_minutes"]) > 0
    assert Decimal(coverage["time_below_p5_minutes"]) >= 0
    assert Decimal(coverage["time_below_p25_minutes"]) >= 0
    assert Decimal(coverage["auc"]["modeled_free_nmol_l_hours"]) == (
        Decimal(coverage["auc"]["regular_modeled_free_nmol_l_hours"])
        + Decimal(coverage["auc"]["stress_modeled_free_nmol_l_hours"])
    )
    assert len(coverage["inter_dose_troughs"]) == 1
    assert coverage["symptom_contexts"] == []
    assert {row["occurred_at"] for row in wake_free_body["samples"]} == {
        row["occurred_at"] for row in wake_free_body["wake_reference"]["samples"]
    }
    assert wake_free_body["supported_dose_count"] == 3
    assert wake_free_body["excluded_dose_count"] == 1
    assert other_dose_id not in {row["dose_event_id"] for row in wake_free_body["dose_markers"]}
    wake_peak_at = next(
        row for row in wake_free_body["dose_markers"] if row["dose_event_id"] == corrected_id
    )["modeled_peak_at"]
    wake_peak = next(row for row in wake_free_body["samples"] if row["occurred_at"] == wake_peak_at)
    assert Decimal(wake_peak["modeled_free_cortisol_nmol_l"]) == (
        Decimal(wake_peak["regular_modeled_free_cortisol_nmol_l"])
        + Decimal(wake_peak["stress_modeled_free_cortisol_nmol_l"])
    )
    assert Decimal(wake_peak["derived_total_cortisol_nmol_l_display"]) > Decimal(
        wake_peak["modeled_free_cortisol_nmol_l"]
    )
    assert "SYNTHETIC_PRIVATE_NOTE_MUST_NOT_APPEAR" not in wake_free.text

    unsupported_model = client.get(
        "/api/v1/analytics/steroid-exposure",
        params={"day": selected_day, "timezone": "UTC", "model": "unknown-model"},
    )
    assert unsupported_model.status_code == 422


def test_wake_free_parameters_are_versioned_owner_scoped_and_audited(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
) -> None:
    defaults = client.get("/api/v1/analytics/wake-free-parameters")
    assert defaults.status_code == 200, defaults.text
    assert defaults.json()["parameters"]["population_default"] is True
    assert defaults.json()["parameters"]["revision_number"] == 0
    assert defaults.json()["parameters"]["elimination_half_life_hours"] == "1.6"
    assert defaults.json()["parameters"]["peak_time_hours"] == "1.1"
    assert defaults.json()["parameters"]["distribution_volume_liters"] == "38.7"
    assert defaults.json()["parameters"]["oral_bioavailability"] == "0.95"

    first_payload = {
        "elimination_half_life_hours": "1.8",
        "peak_time_hours": "1.2",
        "distribution_volume_liters": "40",
        "oral_bioavailability": "0.9",
    }
    without_csrf = client.post(
        "/api/v1/analytics/wake-free-parameters/revisions",
        json=first_payload,
    )
    assert without_csrf.status_code == 403

    first = client.post(
        "/api/v1/analytics/wake-free-parameters/revisions",
        json=first_payload,
        headers=logged_in,
    )
    assert first.status_code == 201, first.text
    assert first.json()["parameters"]["revision_number"] == 1
    assert first.json()["parameters"]["population_default"] is False

    second_payload = {
        "elimination_half_life_hours": "2.1",
        "peak_time_hours": "1.5",
        "distribution_volume_liters": "45",
        "oral_bioavailability": "0.8",
    }
    second = client.post(
        "/api/v1/analytics/wake-free-parameters/revisions",
        json=second_payload,
        headers=logged_in,
    )
    assert second.status_code == 201, second.text
    assert second.json()["parameters"]["revision_number"] == 2
    assert second.json()["parameters"]["peak_time_hours"] == "1.5"

    invalid = client.post(
        "/api/v1/analytics/wake-free-parameters/revisions",
        json={**second_payload, "oral_bioavailability": "1.1"},
        headers=logged_in,
    )
    assert invalid.status_code == 422

    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        revisions = list(
            session.scalars(
                select(all_models.CortisolPkParameterRevision)
                .where(all_models.CortisolPkParameterRevision.owner_id == owner_id)
                .order_by(all_models.CortisolPkParameterRevision.revision_number)
            )
        )
        assert len(revisions) == 2
        assert revisions[0].supersedes_id is None
        assert revisions[1].supersedes_id == revisions[0].id
        audit_entry = session.scalar(
            select(AuditEntry)
            .where(
                AuditEntry.action == AuditAction.CORTISOL_PK_PARAMETERS_REVISED,
                AuditEntry.target_id == revisions[1].id,
            )
            .order_by(AuditEntry.occurred_at.desc())
        )
        assert audit_entry is not None
        assert audit_entry.change_summary == (
            "fields=elimination_half_life_hours,peak_time_hours,"
            "distribution_volume_liters,oral_bioavailability"
        )
        assert "2.1" not in audit_entry.change_summary

        other = Owner(
            email=f"pk-other-{uuid.uuid4()}@example.test",
            password_hash="synthetic-non-login-hash",
            default_timezone="UTC",
        )
        session.add(other)
        session.flush()
        wake_pharmacokinetics.create_parameter_revision(
            session,
            owner_id=other.id,
            elimination_half_life_hours=Decimal("3"),
            peak_time_hours=Decimal("2"),
            distribution_volume_liters=Decimal("60"),
            oral_bioavailability=Decimal("0.7"),
        )

    active = client.get("/api/v1/analytics/wake-free-parameters")
    assert active.status_code == 200, active.text
    assert active.json()["parameters"]["revision_number"] == 2
    assert active.json()["parameters"]["elimination_half_life_hours"] == "2.1"


def test_daily_patterns_recompute_current_facts_and_export_dst_safe_features(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_email = f"dp-{uuid.uuid4().hex[:12]}@example.com"
    day = date(2026, 3, 8)
    with Session(engine) as session, session.begin():
        owner = Owner(
            email=owner_email,
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="America/New_York",
        )
        session.add(owner)
        session.flush()
        medication = Medication(
            owner_id=owner.id,
            name="Hydrocortisone",
            normalized_name="hydrocortisone",
            formulation="tablet",
            strength=Decimal("10"),
            strength_unit="mg",
            default_unit=DoseUnit.MG,
            default_route=Route.ORAL,
        )
        session.add(medication)
        session.flush()
        first_plan = medication_service.create_draft(
            session,
            owner_id=owner.id,
            version_label="Synthetic pre-transition plan",
            effective_from=datetime(2026, 1, 1),  # noqa: DTZ001
            effective_to=datetime(2026, 3, 8, 3),  # noqa: DTZ001
        )
        medication_service.approve_version(
            session,
            first_plan,
            approved_by="Synthetic clinician",
            approval_source="Synthetic dated source",
        )
        second_plan = medication_service.create_draft(
            session,
            owner_id=owner.id,
            version_label="Synthetic post-transition plan",
            effective_from=datetime(2026, 3, 8, 3),  # noqa: DTZ001
        )
        medication_service.approve_version(
            session,
            second_plan,
            approved_by="Synthetic clinician",
            approval_source="Synthetic dated source",
        )

        events.create_event(
            session,
            DoseEvent,
            owner_id=owner.id,
            event_time=resolve_event_time(
                datetime(2026, 3, 8, 1, 30),  # noqa: DTZ001
                "America/New_York",
            ),
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            medication_id=medication.id,
            amount=Decimal("10"),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            category=DoseCategory.SCHEDULED,
            regimen_version_id=first_plan.id,
            slot_id=None,
            episode_id=None,
        )
        second_dose = events.create_event(
            session,
            DoseEvent,
            owner_id=owner.id,
            event_time=resolve_event_time(
                datetime(2026, 3, 8, 3, 30),  # noqa: DTZ001
                "America/New_York",
            ),
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            medication_id=medication.id,
            amount=Decimal("5"),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            category=DoseCategory.SCHEDULED,
            regimen_version_id=second_plan.id,
            slot_id=None,
            episode_id=None,
        )
        symptom = events.create_event(
            session,
            SymptomEvent,
            owner_id=owner.id,
            event_time=resolve_event_time(
                datetime(2026, 3, 8, 4),  # noqa: DTZ001
                "America/New_York",
            ),
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            name="Synthetic dizziness",
            severity=7,
            body_area=None,
            ended_at=None,
            episode_id=None,
            notes=None,
        )
        events.create_event(
            session,
            BloodPressureEvent,
            owner_id=owner.id,
            event_time=resolve_event_time(
                datetime(2026, 3, 8, 4, 10),  # noqa: DTZ001
                "America/New_York",
            ),
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            systolic_mmhg=110,
            diastolic_mmhg=70,
            pulse_bpm=None,
            notes=None,
        )
        temperature = events.create_event(
            session,
            TemperatureEvent,
            owner_id=owner.id,
            event_time=resolve_event_time(
                datetime(2026, 3, 8, 4, 20),  # noqa: DTZ001
                "America/New_York",
            ),
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            value=Decimal("38"),
            unit=TemperatureUnit.CELSIUS,
            normalized_c=Decimal("38"),
            notes=None,
        )
        temperature_id = temperature.id
        session.add(
            StressEpisode(
                owner_id=owner.id,
                trigger="Synthetic DST interval",
                status=EpisodeStatus.RESOLVED,
                severity=EpisodeSeverity.MODERATE,
                started_at=resolve_event_time(
                    datetime(2026, 3, 8, 1, 45),  # noqa: DTZ001
                    "America/New_York",
                ).occurred_at,
                ended_at=resolve_event_time(
                    datetime(2026, 3, 8, 3, 15),  # noqa: DTZ001
                    "America/New_York",
                ).occurred_at,
                timezone="America/New_York",
                recorded_at=datetime.now(UTC),
            )
        )
        sync_run = GarminSyncRun(
            owner_id=owner.id,
            requested_start_date=day,
            requested_end_date=day,
            timezone="America/New_York",
            status=GarminSyncStatus.COMPLETED,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            counts={},
            warning_codes=[],
            client_version="synthetic",
        )
        session.add(sync_run)
        session.flush()

        heart_samples = []
        for index, value in enumerate((Decimal("70"), Decimal("75"))):
            sample = events.create_event(
                session,
                GarminMetricEvent,
                owner_id=owner.id,
                event_time=resolve_event_time(
                    datetime(2026, 3, 8, 3, index * 5),  # noqa: DTZ001
                    "America/New_York",
                ),
                source_type=SourceType.PROVIDER,
                confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
                provider_id=f"synthetic-pattern-heart-{index}",
                source_revision="hr-v1",
                import_batch_id=None,
                garmin_import_batch_id=None,
                garmin_sync_run_id=sync_run.id,
                garmin_source_member="synthetic-intraday",
                garmin_manufacturer="Garmin",
                garmin_product_name=None,
                garmin_device_serial_hash=None,
                metric_type=GarminMetricType.HEART_RATE,
                value=value,
                unit="bpm",
                period_end_at=None,
                aggregation="provider_sample",
                sample_interval_seconds=300,
                garmin_field_name="heartrate",
                notes=None,
            )
            heart_samples.append(sample)
        daily_stress = events.create_event(
            session,
            GarminMetricEvent,
            owner_id=owner.id,
            event_time=resolve_event_time(
                datetime(2026, 3, 8, 0),  # noqa: DTZ001
                "America/New_York",
            ),
            source_type=SourceType.PROVIDER,
            confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
            provider_id="synthetic-pattern-daily-stress",
            source_revision="stress-v1",
            import_batch_id=None,
            garmin_import_batch_id=None,
            garmin_sync_run_id=sync_run.id,
            garmin_source_member="synthetic-daily",
            garmin_manufacturer="Garmin",
            garmin_product_name=None,
            garmin_device_serial_hash=None,
            metric_type=GarminMetricType.STRESS,
            value=Decimal("31"),
            unit="garmin_score",
            period_end_at=None,
            aggregation="daily_summary",
            sample_interval_seconds=None,
            garmin_field_name="averageStressLevel",
            notes=None,
        )
        symptom_id = symptom.id
        first_sample_id = heart_samples[0].id
        daily_stress_id = daily_stress.id
        second_dose_id = second_dose.id
        expected_plan_ids = {str(first_plan.id), str(second_plan.id)}
        owner_id = owner.id

    original_build_curve = exposure.build_curve
    build_curve_calls = 0

    def counted_build_curve(**kwargs: Any) -> dict[str, object]:
        nonlocal build_curve_calls
        build_curve_calls += 1
        return original_build_curve(**kwargs)

    monkeypatch.setattr(exposure, "build_curve", counted_build_curve)
    with Session(engine) as session:
        projection = day_analysis_service.build_projection(
            session,
            owner_id=owner_id,
            day=day,
            timezone="America/New_York",
        )
    assert build_curve_calls == 1
    assert projection["projection_version"] == "hc-day-analysis-v1"
    revision_value = projection["source_revision_sha256"]
    assert isinstance(revision_value, str) and len(revision_value) == 64
    availability = cast(dict[str, int], projection["data_availability_counts"])
    facts = cast(dict[str, list[dict[str, Any]]], projection["recorded_facts_and_plan_context"])
    assert availability["garmin_intraday_samples"] == 2
    assert set(availability) == {
        "doses",
        "symptoms",
        "stress_episodes",
        "emergency_injections",
        "blood_pressure",
        "temperature",
        "weight",
        "diary",
        "life_events",
        "meals",
        "labs",
        "garmin_intraday_samples",
        "garmin_daily_or_point_metrics",
        "garmin_sleep",
        "garmin_activities",
        "context",
        "physician_approved_plans",
    }
    assert len(facts["doses"]) == 2
    assert len(facts["symptoms"]) == 1
    assert len(facts["temperature"]) == 1
    temperature_fact = facts["temperature"][0]
    assert temperature_fact["id"] == str(temperature_id)
    assert temperature_fact["local_time"] == "2026-03-08T04:20:00-04:00"
    assert temperature_fact["entered_value"] == "38.00"
    assert temperature_fact["entered_unit"] == "c"
    assert temperature_fact["fahrenheit"] == "100.4"
    assert temperature_fact["celsius"] == "38.0"
    assert len(facts["physician_approved_plans"]) == 1
    assert facts["physician_approved_plans"][0]["version_label"] == (
        "Synthetic post-transition plan"
    )
    buckets = facts["garmin_intraday_15_minute_buckets"]
    assert len(buckets) == 1
    assert buckets[0]["sample_count"] == 2
    assert buckets[0]["average"] == "72.5000"
    model_inputs = day_analysis_service.build_model_inputs(projection)
    assert model_inputs["model_input_version"] == "hc-day-model-input-v1"
    assert "source_record_ids" not in model_inputs
    model_facts = cast(dict[str, Any], model_inputs["recorded_facts_and_plan_context"])
    compact_buckets = cast(dict[str, Any], model_facts["garmin_intraday_15_minute_buckets"])
    assert compact_buckets["encoding"] == "columnar_rows_v1"
    assert compact_buckets["columns"] == [
        "metric_type",
        "unit",
        "bucket_start_local",
        "sample_count",
        "minimum",
        "average",
        "maximum",
    ]
    assert compact_buckets["rows"] == [
        ["heart_rate", "bpm", "2026-03-08T03:00:00-04:00", 2, "70.0000", "72.5000", "75.0000"]
    ]

    login = client.post("/api/v1/auth/login", json={"email": owner_email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    response = client.get(
        "/api/v1/analytics/daily-patterns",
        params={
            "date_from": day.isoformat(),
            "date_to": (day + timedelta(days=1)).isoformat(),
            "timezone": "America/New_York",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["feature_version"] == "hc-daily-pattern-v1"
    assert body["exposure_model_versions"] == ["hc-exposure-v1"]
    assert "do not establish causation" in body["safety_label"]
    selected = body["days"][0]
    assert selected["elapsed_hours"] == "23.0"
    assert set(selected["dose_plan_version_ids"]) == expected_plan_ids
    assert selected["supported_dose_count"] == 2
    assert Decimal(selected["exposure_auc_reu_hours"]) > 0
    assert selected["symptom_count"] == 1
    timing = selected["symptom_timings"][0]
    assert timing["symptom_event_id"] == str(symptom_id)
    assert timing["previous_supported_dose_event_ids"] == [str(second_dose_id)]
    assert timing["minutes_since_previous_supported_dose"] == "30.0000"
    assert Decimal(timing["theoretical_exposure_reu"]) > 0
    heart = next(row for row in selected["wearables"] if row["metric_type"] == "heart_rate")
    assert heart["sample_count"] == 2
    assert heart["average"] == "72.5000"
    assert heart["observed_coverage_minutes"] == "10.0000"
    assert heart["observed_coverage_percent"] == "0.7246"
    hrv = next(row for row in selected["wearables"] if row["metric_type"] == "hrv")
    assert hrv["sample_count"] == 0
    assert hrv["minimum"] is None
    assert hrv["missingness_state"] == "no_samples"
    assert selected["blood_pressure"]["pulse_missing_count"] == 1
    assert selected["blood_pressure"]["pulse"]["average"] is None
    assert selected["stress_episodes"]["overlap_minutes"] == "30.0000"
    assert body["days"][1]["symptom_count"] == 0
    original_watermark = selected["source_revision_watermark_sha256"]

    with Session(engine) as session:
        cached = session.scalar(
            select(WearableDailySummary).where(
                WearableDailySummary.owner_id == owner_id,
                WearableDailySummary.local_date == day,
                WearableDailySummary.timezone == "America/New_York",
                WearableDailySummary.metric_type == GarminMetricType.HEART_RATE,
            )
        )
        assert cached is not None
        cached_summary_id = cached.id
        cached_refreshed_at = cached.refreshed_at
        original_summary_watermark = cached.source_revision_watermark_sha256

    export = client.get(
        "/api/v1/analytics/daily-patterns.csv",
        params={
            "date_from": day.isoformat(),
            "date_to": (day + timedelta(days=1)).isoformat(),
            "timezone": "America/New_York",
        },
    )
    assert export.status_code == 200, export.text
    assert export.headers["content-type"].startswith("text/csv")
    assert "attachment;" in export.headers["content-disposition"]
    assert "source_revision_watermark_sha256" in export.text
    assert "heart_rate_observed_coverage_percent" in export.text

    with Session(engine) as session:
        rerun_cached = session.scalar(
            select(WearableDailySummary).where(
                WearableDailySummary.owner_id == owner_id,
                WearableDailySummary.local_date == day,
                WearableDailySummary.timezone == "America/New_York",
                WearableDailySummary.metric_type == GarminMetricType.HEART_RATE,
            )
        )
        assert rerun_cached is not None
        assert rerun_cached.id == cached_summary_id
        assert rerun_cached.refreshed_at == cached_refreshed_at

    with Session(engine) as session, session.begin():
        report = report_builder.build_snapshot(
            session,
            owner_id=owner_id,
            date_from=day,
            date_to=day + timedelta(days=1),
            timezone="America/New_York",
            selected_sections=["wearables"],
        )
        assert not any(
            row.get("record_type") == "garmin_metric"
            for row in cast(list[dict[str, Any]], report.snapshot_content["fact"])
        )
        report_facts = cast(list[dict[str, Any]], report.snapshot_content["fact"])
        report_aggregate = next(
            row for row in report_facts if row.get("record_type") == "garmin_metric_aggregate"
        )
        assert report_aggregate["id"] == str(daily_stress_id)
        assert report_aggregate["value"] == "31.0000"
        assert report_aggregate["aggregation"] == "daily_summary"
        report_summary = cast(dict[str, Any], report.metric_values["wearable_daily_summaries"])
        assert report_summary["summary_version"] == "hc-wearable-daily-v1"
        summary_values = cast(list[dict[str, Any]], report_summary["values"])
        assert len(summary_values) == 8
        report_heart = next(
            row
            for row in summary_values
            if row["date"] == day.isoformat() and row["metric_type"] == "heart_rate"
        )
        assert report_heart["sample_count"] == 2

    with Session(engine) as session, session.begin():
        original_sample = session.get(GarminMetricEvent, first_sample_id)
        assert original_sample is not None
        events.correct_event(
            session,
            GarminMetricEvent,
            original_sample,
            reason="Synthetic provider revision",
            changes={"value": Decimal("80"), "source_revision": "hr-v2"},
        )

    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(WearableDailySummary)
                .where(
                    WearableDailySummary.owner_id == owner_id,
                    WearableDailySummary.local_date == day,
                    WearableDailySummary.metric_type == GarminMetricType.HEART_RATE,
                )
            )
            == 0
        )

    revised = client.get(
        "/api/v1/analytics/daily-patterns",
        params={
            "date_from": day.isoformat(),
            "date_to": day.isoformat(),
            "timezone": "America/New_York",
        },
    )
    assert revised.status_code == 200, revised.text
    revised_day = revised.json()["days"][0]
    revised_heart = next(
        row for row in revised_day["wearables"] if row["metric_type"] == "heart_rate"
    )
    assert revised_heart["sample_count"] == 2
    assert revised_heart["average"] == "77.5000"
    assert revised_heart["source_revision_watermark_sha256"] != original_summary_watermark
    assert revised_day["source_revision_watermark_sha256"] != original_watermark

    too_long = client.get(
        "/api/v1/analytics/daily-patterns",
        params={"date_from": "2025-01-01", "date_to": "2026-01-02", "timezone": "UTC"},
    )
    assert too_long.status_code == 422

    performance_params = {
        "date_from": (day - timedelta(days=365)).isoformat(),
        "date_to": day.isoformat(),
        "timezone": "America/New_York",
    }
    assert (
        client.get("/api/v1/analytics/daily-patterns", params=performance_params).status_code == 200
    )
    elapsed = []
    for _ in range(3):
        started = perf_counter()
        measured = client.get("/api/v1/analytics/daily-patterns", params=performance_params)
        elapsed.append(perf_counter() - started)
        assert measured.status_code == 200
        assert len(measured.json()["days"]) == 366
    assert median(elapsed) < 2.0


def test_pattern_analysis_fails_safely_when_private_model_is_unavailable(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_email = f"pattern-ai-{uuid.uuid4().hex[:12]}@example.com"
    with Session(engine) as session, session.begin():
        session.add(
            Owner(
                email=owner_email,
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="UTC",
            )
        )

    login = client.post("/api/v1/auth/login", json={"email": owner_email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]

    def unavailable(*args: object, **kwargs: object) -> analysis_service.AnalysisGenerationResult:
        return analysis_service.AnalysisGenerationResult(
            outcome=analysis_service.AnalysisOutcome.MODEL_UNAVAILABLE,
            detail="synthetic private model detail must not cross the API",
        )

    monkeypatch.setattr(analysis_service, "generate_analysis", unavailable)
    response = client.post(
        "/api/v1/analytics/pattern-analysis",
        params={"date_from": "2026-08-01", "date_to": "2026-08-07", "timezone": "UTC"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json() == {
        "outcome": "model_unavailable",
        "detail": (
            "The configured private model is unavailable. Deterministic results remain available."
        ),
        "analysis": None,
    }
    assert "synthetic private model detail" not in response.text


def test_pattern_analysis_completion_is_reloadable_by_exact_range_and_timeout_is_safe(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_email = f"pattern-recovery-{uuid.uuid4().hex[:12]}@example.com"
    with Session(engine) as session, session.begin():
        session.add(
            Owner(
                email=owner_email,
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="America/New_York",
            )
        )

    login = client.post("/api/v1/auth/login", json={"email": owner_email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]

    def generated(session: Session, **kwargs: object) -> analysis_service.AnalysisGenerationResult:
        assert kwargs["system_prompt"] == analysis_service.PATTERN_SYSTEM_PROMPT
        assert kwargs["prompt_version"] == analysis_service.PATTERN_PROMPT_VERSION
        assert kwargs["max_output_tokens"] == analysis_service.PATTERN_MAX_OUTPUT_TOKENS
        assert kwargs["context_window"] == analysis_service.PATTERN_CONTEXT_WINDOW
        assert kwargs["read_timeout_s"] == analysis_service.PATTERN_READ_TIMEOUT_SECONDS
        assert kwargs["deterministic_safety_fields"] is True
        row = AIAnalysis(
            owner_id=cast(uuid.UUID, kwargs["owner_id"]),
            analysis_type=AnalysisType.PATTERN_OBSERVATION,
            body="Synthetic checked pattern explanation.",
            source_record_ids=cast(list[str], kwargs["source_record_ids"]),
            computed_inputs=cast(dict[str, object], kwargs["computed_inputs"]),
            model_name="synthetic-local-model",
            model_digest="sha256:synthetic",
            prompt_version=cast(str, kwargs["prompt_version"]),
            schema_version="analysis-v1",
        )
        session.add(row)
        session.flush()
        return analysis_service.AnalysisGenerationResult(
            outcome=analysis_service.AnalysisOutcome.CREATED,
            analysis=row,
        )

    monkeypatch.setattr(analysis_service, "generate_analysis", generated)
    params = {
        "date_from": "2026-08-01",
        "date_to": "2026-08-07",
        "timezone": "America/New_York",
    }
    created = client.post(
        "/api/v1/analytics/pattern-analysis",
        params=params,
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200, created.text
    assert created.json()["outcome"] == "created"
    assert created.json()["analysis"]["computed_inputs"]["selected_date_from"] == "2026-08-01"
    assert created.json()["analysis"]["computed_inputs"]["selected_date_to"] == "2026-08-07"
    assert created.json()["analysis"]["computed_inputs"]["selected_timezone"] == "America/New_York"

    reloaded = client.get("/api/v1/analytics/pattern-analysis", params=params)
    assert reloaded.status_code == 200, reloaded.text
    assert [row["id"] for row in reloaded.json()] == [created.json()["analysis"]["id"]]
    other_range = client.get(
        "/api/v1/analytics/pattern-analysis",
        params={**params, "date_from": "2026-08-02"},
    )
    assert other_range.status_code == 200, other_range.text
    assert other_range.json() == []
    other_timezone = client.get(
        "/api/v1/analytics/pattern-analysis",
        params={**params, "timezone": "America/Toronto"},
    )
    assert other_timezone.status_code == 200, other_timezone.text
    assert other_timezone.json() == []
    incomplete_range = client.get(
        "/api/v1/analytics/pattern-analysis",
        params={"date_from": "2026-08-01"},
    )
    assert incomplete_range.status_code == 422

    def timed_out_generation(
        session: Session, **kwargs: object
    ) -> analysis_service.AnalysisGenerationResult:
        del session, kwargs
        return analysis_service.AnalysisGenerationResult(
            outcome=analysis_service.AnalysisOutcome.MODEL_TIMEOUT,
            detail="synthetic timeout detail must not cross the API",
        )

    monkeypatch.setattr(analysis_service, "generate_analysis", timed_out_generation)
    timed_out = client.post(
        "/api/v1/analytics/pattern-analysis",
        params={**params, "date_from": "2026-08-08", "date_to": "2026-08-08"},
        headers={"X-CSRF-Token": csrf},
    )
    assert timed_out.status_code == 200, timed_out.text
    assert timed_out.json() == {
        "outcome": "model_timeout",
        "detail": (
            "The configured private model did not finish within HealthCurve's time limit. "
            "Deterministic results remain available."
        ),
        "analysis": None,
    }
    assert "synthetic timeout detail" not in timed_out.text


def test_day_analysis_persists_provenance_and_detects_late_data(
    client: TestClient, engine: Engine, monkeypatch: pytest.MonkeyPatch
) -> None:
    owner_email = f"day-ai-{uuid.uuid4().hex[:12]}@example.com"
    with Session(engine) as session, session.begin():
        owner = Owner(
            email=owner_email,
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(owner)

    login = client.post("/api/v1/auth/login", json={"email": owner_email, "password": PASSWORD})
    assert login.status_code == 200, login.text
    csrf = login.json()["csrf_token"]
    revision = "a" * 64

    def projection(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "projection_version": "hc-day-analysis-v1",
            "selected_local_date": "2026-08-11",
            "selected_timezone": "UTC",
            "data_availability_counts": {"symptoms": 1},
            "missing_domains": ["labs"],
            "recorded_facts_and_plan_context": {
                "diary": "SYNTHETIC_PRIVATE_DAY_TEXT",
                "garmin_intraday_15_minute_buckets": [],
            },
            "theoretical_exposure_15_minute_buckets": [],
            "source_revision_sha256": revision,
            "source_record_id": f"healthcurve-day:2026-08-11:{revision}",
            "source_record_ids": ["11111111-1111-4111-8111-111111111111"],
        }

    def generated(session: Session, **kwargs: object) -> analysis_service.AnalysisGenerationResult:
        assert kwargs["deterministic_safety_fields"] is True
        assert kwargs["compact_output"] is True
        assert kwargs["max_output_tokens"] == analysis_service.DAY_MAX_OUTPUT_TOKENS
        assert kwargs["context_window"] == analysis_service.DAY_CONTEXT_WINDOW
        row = AIAnalysis(
            owner_id=kwargs["owner_id"],
            analysis_type=AnalysisType.DAILY_SUMMARY,
            body=(
                "- Synthetic temporal association. [sources: healthcurve-day]"
                "\n\nMissingness: labs missing.\n"
                "Correlation caution: association does not establish causation or diagnosis."
            ),
            source_record_ids=kwargs["persisted_source_record_ids"],
            computed_inputs=kwargs["persisted_inputs"],
            model_name="qwen3:30b",
            model_digest="sha256:synthetic",
            prompt_version=kwargs["prompt_version"],
            schema_version="analysis-v1",
        )
        session.add(row)
        session.flush()
        return analysis_service.AnalysisGenerationResult(
            outcome=analysis_service.AnalysisOutcome.CREATED,
            analysis=row,
        )

    monkeypatch.setattr(day_analysis_service, "build_projection", projection)
    monkeypatch.setattr(analysis_service, "generate_analysis", generated)
    created = client.post(
        "/api/v1/analytics/day-analysis",
        params={"day": "2026-08-11", "timezone": "UTC"},
        headers={"X-CSRF-Token": csrf},
    )
    assert created.status_code == 200, created.text
    body = created.json()
    assert body["outcome"] == "created"
    assert body["analysis"]["category"] == "ai"
    assert body["analysis"]["analysis_type"] == "daily_summary"
    assert body["analysis"]["source_revision_sha256"] == revision
    assert body["analysis"]["source_record_count"] == 2
    assert body["analysis"]["prompt_version"] == "healthcurve-day-analysis-v4"
    assert body["analysis"]["stale"] is False
    with Session(engine) as session:
        retained = session.scalar(
            select(AIAnalysis).where(AIAnalysis.id == uuid.UUID(body["analysis"]["id"]))
        )
        assert retained is not None
        retained_inputs = retained.computed_inputs
        assert retained_inputs is not None
        assert "SYNTHETIC_PRIVATE_DAY_TEXT" not in json.dumps(retained_inputs)
        assert retained_inputs["model_input_version"] == "hc-day-model-input-v1"

    revision = "b" * 64
    stale = client.get(
        "/api/v1/analytics/day-analysis",
        params={"day": "2026-08-11", "timezone": "UTC"},
    )
    assert stale.status_code == 200, stale.text
    assert stale.json()["stale"] is True


@pytest.mark.parametrize(
    ("outcome", "safe_fragment"),
    [
        (analysis_service.AnalysisOutcome.MODEL_UNAVAILABLE, "could not reach"),
        (analysis_service.AnalysisOutcome.MODEL_TIMEOUT, "did not finish"),
        (analysis_service.AnalysisOutcome.MODEL_INVALID_RESPONSE, "malformed structured output"),
        (analysis_service.AnalysisOutcome.INVALID, "unsupported values"),
    ],
)
def test_day_analysis_failure_paths_do_not_leak_model_details(
    client: TestClient,
    engine: Engine,
    monkeypatch: pytest.MonkeyPatch,
    outcome: analysis_service.AnalysisOutcome,
    safe_fragment: str,
) -> None:
    owner_email = f"day-ai-failure-{uuid.uuid4().hex[:10]}@example.com"
    with Session(engine) as session, session.begin():
        session.add(
            Owner(
                email=owner_email,
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="UTC",
            )
        )
    login = client.post("/api/v1/auth/login", json={"email": owner_email, "password": PASSWORD})
    csrf = login.json()["csrf_token"]

    def empty_projection(*args: object, **kwargs: object) -> dict[str, object]:
        return {
            "projection_version": "hc-day-analysis-v1",
            "selected_local_date": "2026-08-11",
            "selected_timezone": "UTC",
            "data_availability_counts": {},
            "missing_domains": ["all supported domains"],
            "recorded_facts_and_plan_context": {"garmin_intraday_15_minute_buckets": []},
            "theoretical_exposure_15_minute_buckets": [],
            "source_revision_sha256": "c" * 64,
            "source_record_id": f"healthcurve-day:2026-08-11:{'c' * 64}",
            "source_record_ids": [],
        }

    def failed_generation(
        *args: object, **kwargs: object
    ) -> analysis_service.AnalysisGenerationResult:
        return analysis_service.AnalysisGenerationResult(
            outcome=outcome,
            detail="synthetic private model detail must not cross the API",
        )

    monkeypatch.setattr(day_analysis_service, "build_projection", empty_projection)
    monkeypatch.setattr(analysis_service, "generate_analysis", failed_generation)
    response = client.post(
        "/api/v1/analytics/day-analysis",
        params={"day": "2026-08-11", "timezone": "UTC"},
        headers={"X-CSRF-Token": csrf},
    )
    assert response.status_code == 200, response.text
    assert response.json()["outcome"] == outcome.value
    assert safe_fragment in response.json()["detail"]
    assert response.json()["analysis"] is None
    assert "synthetic private model detail" not in response.text


def test_data_quality_distinguishes_problems_from_provider_absence(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        session.add(
            ExtractionDraft(
                owner_id=owner_id,
                source="telegram",
                provider_message_id="synthetic-quality-draft",
                raw_text="SYNTHETIC_TEST_DATA",
                candidates=[{"type": "dose", "flags": ["possible_duplicate"]}],
                state=DraftState.PENDING,
                prompt_version="synthetic",
                schema_version="synthetic",
            )
        )
        session.add(
            LabDocument(
                owner_id=owner_id,
                display_name="synthetic-rejected.pdf",
                media_type="application/pdf",
                sha256="b" * 64,
                byte_size=1,
                status=LabDocumentStatus.REJECTED,
                rejection_reason="synthetic_validation_failure",
            )
        )
        session.add(
            GarminImportBatch(
                owner_id=owner_id,
                source_name="synthetic-quality.fit",
                source_media_type="application/octet-stream",
                source_sha256="c" * 64,
                source_byte_size=1,
                source_payload=b"q",
                source_members=[],
                sdk_profile_version="synthetic",
                observed_metrics=[],
                missing_metrics=["hrv"],
                device_attributions=[],
            )
        )
        session.add(
            Job(
                task="synthetic.quality",
                payload={},
                idempotency_key="synthetic-quality-dead-letter",
                status=JobStatus.DEAD_LETTER,
                attempt_count=3,
                max_attempts=3,
                last_error_code="synthetic_failure",
            )
        )

    response = client.get("/api/v1/data-quality")
    assert response.status_code == 200, response.text
    body = response.json()
    titles = {finding["title"] for finding in body["findings"]}
    assert "Possible duplicate draft" in titles
    assert "Lab document import failed" in titles
    assert "Hrv not supplied" in titles
    assert "Background task exhausted retries" in titles
    absence = next(f for f in body["findings"] if f["title"] == "Hrv not supplied")
    assert absence["finding_kind"] == "genuine_absence"
    assert "no zero is inferred" in absence["detail"]
    for finding in body["findings"]:
        assert finding["href"].startswith("/")
        assert finding["action_label"]
    assert "does not mean" in body["completeness_notice"]


def test_data_quality_flags_only_owner_scoped_open_episodes_at_24_hour_boundary(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    del logged_in
    now = datetime(2026, 8, 12, 16, 0, tzinfo=UTC)
    email = f"episode-boundary-{uuid.uuid4()}@example.com"
    with Session(engine) as session, session.begin():
        owner = Owner(
            email=email,
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="America/New_York",
        )
        other = Owner(
            email="other-open-episode@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add_all([owner, other])
        session.flush()
        owner_id = owner.id
        episodes = [
            StressEpisode(
                owner_id=owner_id,
                trigger="Synthetic boundary episode",
                status=EpisodeStatus.OPEN,
                severity=EpisodeSeverity.MILD,
                started_at=now - timedelta(hours=24),
                ended_at=None,
                timezone="America/New_York",
                recorded_at=now - timedelta(hours=24),
            ),
            StressEpisode(
                owner_id=owner_id,
                trigger="Synthetic recent episode",
                status=EpisodeStatus.OPEN,
                severity=None,
                started_at=now - timedelta(hours=23, minutes=59),
                ended_at=None,
                timezone="America/New_York",
                recorded_at=now - timedelta(hours=23, minutes=59),
            ),
            StressEpisode(
                owner_id=owner_id,
                trigger="Synthetic resolved episode",
                status=EpisodeStatus.RESOLVED,
                severity=None,
                started_at=now - timedelta(days=3),
                ended_at=now - timedelta(days=2),
                timezone="UTC",
                recorded_at=now - timedelta(days=3),
            ),
            StressEpisode(
                owner_id=owner_id,
                trigger="Synthetic escalated episode",
                status=EpisodeStatus.ESCALATED,
                severity=EpisodeSeverity.SEVERE,
                started_at=now - timedelta(days=3),
                ended_at=None,
                timezone="UTC",
                recorded_at=now - timedelta(days=3),
            ),
            StressEpisode(
                owner_id=other.id,
                trigger="Other owner's old open episode",
                status=EpisodeStatus.OPEN,
                severity=None,
                started_at=now - timedelta(days=4),
                ended_at=None,
                timezone="UTC",
                recorded_at=now - timedelta(days=4),
            ),
        ]
        session.add_all(episodes)
        session.flush()
        boundary_id = episodes[0].id
        other_id = episodes[-1].id

        findings = findings_for_owner(session, owner_id, now=now)

    login = client.post("/api/v1/auth/login", json={"email": email, "password": PASSWORD})
    assert login.status_code == 200, login.text

    episode_findings = [item for item in findings if item.id.startswith("open-episode:")]
    assert len(episode_findings) == 1
    finding = episode_findings[0]
    assert finding.record_id == boundary_id
    assert finding.detail == (
        "“Synthetic boundary episode” started Aug 11, 2026 at 12:00 EDT and has "
        "remained open for 1 day. Confirm that it is still continuing or record its "
        "actual end time; HealthCurve has not inferred an end."
    )
    assert finding.href == (
        f"/episodes?history=all&review_episode={boundary_id}#episode-{boundary_id}"
    )
    assert finding.action_label == "Review or close episode"

    selected = client.get(
        f"/api/v1/stress-episodes?status_filter=open&episode_id={boundary_id}&history=all"
    )
    assert selected.status_code == 200
    assert [item["id"] for item in selected.json()["items"]] == [str(boundary_id)]
    forbidden = client.get(
        f"/api/v1/stress-episodes?status_filter=open&episode_id={other_id}&history=all"
    )
    assert forbidden.status_code == 200
    assert forbidden.json()["items"] == []

    with Session(engine) as session, session.begin():
        boundary = session.get(StressEpisode, boundary_id)
        assert boundary is not None
        assert boundary.ended_at is None
        boundary.status = EpisodeStatus.RESOLVED
        boundary.ended_at = now - timedelta(hours=1)
    with Session(engine) as session:
        corrected_findings = findings_for_owner(session, owner_id, now=now)
    assert all(item.record_id != boundary_id for item in corrected_findings)


def test_data_quality_groups_and_acknowledges_latest_garmin_sync_warning(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        connection = session.scalar(
            select(GarminConnection).where(GarminConnection.owner_id == owner_id)
        )
        if connection is None:
            connection = GarminConnection(
                owner_id=owner_id,
                state=GarminConnectionState.CONNECTED,
                connected_at=datetime(2026, 8, 1, tzinfo=UTC),
                capabilities={},
                client_version="synthetic",
            )
            session.add(connection)
        else:
            connection.state = GarminConnectionState.CONNECTED
            connection.connected_at = datetime(2026, 8, 1, tzinfo=UTC)
            connection.disconnected_at = None
            connection.capabilities = {}
            connection.client_version = "synthetic"
        run = GarminSyncRun(
            owner_id=owner_id,
            requested_start_date=date(2026, 8, 5),
            requested_end_date=date(2026, 8, 11),
            timezone="America/New_York",
            origin=GarminSyncOrigin.MANUAL_REFRESH,
            status=GarminSyncStatus.COMPLETED_WITH_WARNINGS,
            started_at=datetime(2026, 8, 11, 8, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 11, 8, 1, tzinfo=UTC),
            counts={"created": 9, "corrected": 2, "unchanged": 40},
            warning_codes=[
                "intraday_heart_rate_missing_or_invalid",
                "intraday_respiration_rate_missing_or_invalid",
                "intraday_stress_missing_or_invalid",
            ],
            client_version="synthetic",
        )
        session.add(run)
        session.flush()
        run_id = run.id

        other = Owner(
            email="other-quality-owner@example.test",
            password_hash=auth.hash_password(PASSWORD),
            default_timezone="UTC",
        )
        session.add(other)
        session.flush()
        other_run = GarminSyncRun(
            owner_id=other.id,
            requested_start_date=date(2026, 8, 11),
            requested_end_date=date(2026, 8, 11),
            timezone="UTC",
            status=GarminSyncStatus.COMPLETED_WITH_WARNINGS,
            started_at=datetime(2026, 8, 11, 9, 0, tzinfo=UTC),
            finished_at=datetime(2026, 8, 11, 9, 1, tzinfo=UTC),
            counts={},
            warning_codes=["intraday_stress_missing_or_invalid"],
            client_version="synthetic",
        )
        session.add(other_run)
        session.flush()
        other_run_id = other_run.id

    response = client.get("/api/v1/data-quality")
    assert response.status_code == 200
    garmin_findings = [
        finding
        for finding in response.json()["findings"]
        if finding["id"] == f"garmin-sync:{run_id}"
    ]
    assert len(garmin_findings) == 1
    finding = garmin_findings[0]
    assert finding["title"] == "Garmin sync completed with 3 data warnings"
    assert finding["source"] == "Garmin Connect · manual refresh"
    assert finding["can_acknowledge"] is True
    assert "Request origin: Manual refresh" in finding["detail"]
    assert "not queued or running work" in finding["detail"]
    assert "2026-08-05 through 2026-08-11" in finding["detail"]
    assert "intraday respiration was missing or unusable" in finding["detail"]
    assert "9 new, 2 corrected, 40 unchanged" in finding["detail"]

    forbidden = client.post(
        f"/api/v1/data-quality/garmin-syncs/{other_run_id}/acknowledge", headers=logged_in
    )
    assert forbidden.status_code == 404
    acknowledged = client.post(
        f"/api/v1/data-quality/garmin-syncs/{run_id}/acknowledge", headers=logged_in
    )
    assert acknowledged.status_code == 204
    assert all(
        item["id"] != f"garmin-sync:{run_id}"
        for item in client.get("/api/v1/data-quality").json()["findings"]
    )
    with Session(engine) as session:
        entry = session.scalar(
            select(AuditEntry).where(
                AuditEntry.action == AuditAction.DATA_QUALITY_ACKNOWLEDGED,
                AuditEntry.target_id == run_id,
            )
        )
        assert entry is not None
        assert entry.change_summary == "reviewed Garmin sync warning notice"


def test_comparison_exposes_plan_fields_needed_for_explicit_dose_capture(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    medication_id = _a_medication(client, logged_in)
    version = client.post(
        "/api/v1/regimens",
        json={
            "version_label": "synthetic comparison plan",
            "effective_from": "2026-01-01T00:00:00",
            "effective_to": "2026-12-31T00:00:00",
            "slots": [
                {
                    "medication_id": medication_id,
                    "scheduled_local_time": "07:00:00",
                    "amount": "10",
                    "unit": "mg",
                    "route": "oral",
                }
            ],
            "instructions": [],
        },
        headers=logged_in,
    )
    assert version.status_code == 201, version.text
    approved = client.post(
        f"/api/v1/regimens/{version.json()['id']}/approve",
        json={"approved_by": "Dr Synthetic", "approval_source": "synthetic fixture"},
        headers=logged_in,
    )
    assert approved.status_code == 200, approved.text

    body = client.get(
        "/api/v1/doses/plan-comparison",
        params={"day": "2026-05-02", "timezone": "Europe/London"},
    ).json()

    planned_slot = next(slot for slot in body["slots"] if slot["slot_id"] is not None)
    assert set(planned_slot) >= {"medication_id", "unit", "route"}
    assert planned_slot["unit"] == "mg"
    assert planned_slot["route"] == "oral"


# ---------------------------------------------------------------------------
# Development bootstrap recovery
# ---------------------------------------------------------------------------


def test_owner_recovery_preserves_all_non_identity_tables_and_revokes_sessions(
    client: TestClient, engine: Engine
) -> None:
    """Recovery changes login state in place; every data-domain row survives."""
    _ = client  # Starts the module-scoped app fixture, which creates the owner.
    allowed_to_change = {
        "identity.owner",
        "identity.auth_session",
        "ops.audit_entry",
    }
    protected_tables = [
        table
        for table in all_models.Base.metadata.sorted_tables
        if table.fullname not in allowed_to_change
    ]

    connection = engine.connect()
    transaction = connection.begin()
    try:
        with Session(bind=connection, expire_on_commit=False) as session:
            owner = session.scalar(select(Owner).limit(1))
            assert owner is not None
            auth.create_session(session, owner, user_agent="synthetic recovery test")
            session.flush()
            before = {
                table.fullname: session.scalar(select(func.count()).select_from(table))
                for table in protected_tables
            }

            revoked = recover_owner_access(
                session,
                owner,
                environment=Environment.DEV,
                new_email="recovered-owner@example.com",
                new_password=PASSWORD,
            )
            session.flush()

            after = {
                table.fullname: session.scalar(select(func.count()).select_from(table))
                for table in protected_tables
            }
            audit_entry = session.scalar(
                select(AuditEntry).where(AuditEntry.action == AuditAction.OWNER_ACCESS_RECOVERED)
            )
            assert revoked >= 1
            assert before == after
            assert audit_entry is not None
            assert PASSWORD not in (audit_entry.change_summary or "")
            assert "recovered-owner@example.com" not in (audit_entry.change_summary or "")
    finally:
        transaction.rollback()
        connection.close()


# ---------------------------------------------------------------------------
# SAFE-21: the emergency page survives everything else being down
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-21")
def test_emergency_page_renders_without_ai_or_javascript(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    _a_medication(client, logged_in)
    response = client.get("/emergency")
    assert response.status_code == 200
    body = response.text
    assert "<script" not in body, "the emergency page must not depend on JavaScript"
    assert "emergency services" in body.lower()
    assert response.headers["cache-control"] == "no-store"
    assert f"name='csrf_token' value='{logged_in[auth.CSRF_HEADER_NAME]}'" in body


@pytest.mark.safety("SAFE-21")
def test_emergency_injection_form_rejects_cross_session_and_missing_csrf(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    medication_id = _a_medication(client, logged_in)
    form = {"medication_id": medication_id, "amount": "100"}

    with Session(engine) as session:
        event_count_before = (
            session.scalar(select(func.count()).select_from(EmergencyInjectionEvent)) or 0
        )
        audit_count_before = (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(
                    AuditEntry.action == AuditAction.RECORD_CREATED,
                    AuditEntry.target_type == EmergencyInjectionEvent.__tablename__,
                )
            )
            or 0
        )

    missing = client.post("/emergency/injection", data=form)
    wrong = client.post(
        "/emergency/injection", data={**form, "csrf_token": "not-this-session-token"}
    )

    second_login = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert second_login.status_code == 200
    second_csrf = second_login.json()["csrf_token"]
    other_session = client.post(
        "/emergency/injection",
        data={**form, "csrf_token": logged_in[auth.CSRF_HEADER_NAME]},
    )

    assert missing.status_code == 403
    assert wrong.status_code == 403
    assert other_session.status_code == 403
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(EmergencyInjectionEvent)) == (
            event_count_before
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(
                    AuditEntry.action == AuditAction.RECORD_CREATED,
                    AuditEntry.target_type == EmergencyInjectionEvent.__tablename__,
                )
            )
            == audit_count_before
        )

    valid = client.post(
        "/emergency/injection",
        data={**form, "csrf_token": second_csrf},
        follow_redirects=False,
    )
    assert valid.status_code == 303
    assert valid.headers["location"] == "/emergency?logged=1"

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(EmergencyInjectionEvent)) == (
            event_count_before + 1
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(AuditEntry)
                .where(
                    AuditEntry.action == AuditAction.RECORD_CREATED,
                    AuditEntry.target_type == EmergencyInjectionEvent.__tablename__,
                )
            )
            == audit_count_before + 1
        )


def test_cookie_authenticated_unsafe_routes_have_csrf_review(client: TestClient) -> None:
    """Inventory cookie-authenticated writes so a new route cannot silently skip CSRF."""

    def has_dependency(route: APIRoute, target: Any) -> bool:
        pending = [route.dependant]
        while pending:
            dependant = pending.pop()
            if dependant.call is target:
                return True
            pending.extend(dependant.dependencies)
        return False

    exceptions = {
        ("/api/v1/auth/login", "POST"),  # no session exists yet
        ("/emergency/injection", "POST"),  # session-bound HTML form token, tested above
    }
    missing: set[tuple[str, str]] = set()
    application = cast(FastAPI, client.app)
    for route in application.routes:
        if not isinstance(route, APIRoute):
            continue
        for method in (route.methods or set()) & {"POST", "PUT", "PATCH", "DELETE"}:
            key = (route.path, method)
            if key in exceptions:
                continue
            if not has_dependency(route, api_deps.require_csrf):
                missing.add(key)
    assert missing == set()


@pytest.mark.safety("SAFE-22")
def test_emergency_page_says_so_when_no_instructions_exist(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    """HealthCurve must never invent emergency instructions."""
    body = client.get("/emergency").text
    assert "No physician-authored emergency instructions" in body
    assert "will not invent" in body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _legacy_medication_bootstrap(
    session: Session, *, owner_id: uuid.UUID, approved: bool
) -> dict[str, Any]:
    """Create the exact, explicitly synthetic legacy template inside a test transaction."""
    medications = [
        Medication(
            owner_id=owner_id,
            name="Hydrocortisone",
            normalized_name="hydrocortisone",
            formulation="tablet",
            strength=Decimal("10"),
            strength_unit="mg",
            default_unit=DoseUnit.MG,
            default_route=Route.ORAL,
        ),
        Medication(
            owner_id=owner_id,
            name="Fludrocortisone",
            normalized_name="fludrocortisone",
            formulation="tablet",
            strength=Decimal("0.1"),
            strength_unit="mg",
            default_unit=DoseUnit.MG,
            default_route=Route.ORAL,
        ),
        Medication(
            owner_id=owner_id,
            name="Hydrocortisone sodium succinate",
            normalized_name="hydrocortisone sodium succinate",
            formulation="injection",
            strength=Decimal("100"),
            strength_unit="mg",
            default_unit=DoseUnit.MG,
            default_route=Route.INTRAMUSCULAR,
        ),
    ]
    session.add_all(medications)
    session.flush()
    by_name = {row.normalized_name: row for row in medications}
    regimen = medication_service.create_draft(
        session,
        owner_id=owner_id,
        version_label="2026 replacement schedule",
        effective_from=datetime(2026, 1, 1, tzinfo=UTC),
    )
    slots = [
        RegimenDoseSlot(
            regimen_version_id=regimen.id,
            medication_id=by_name["hydrocortisone"].id,
            scheduled_local_time=time.fromisoformat(clock),
            amount=Decimal(amount),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            sort_order=0,
        )
        for clock, amount in (("07:00", "10"), ("12:30", "5"), ("17:00", "2.5"))
    ]
    slots.append(
        RegimenDoseSlot(
            regimen_version_id=regimen.id,
            medication_id=by_name["fludrocortisone"].id,
            scheduled_local_time=time(7, 0),
            amount=Decimal("0.1"),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            sort_order=0,
        )
    )
    instructions = [
        ApprovedInstruction(
            regimen_version_id=regimen.id,
            category=category,
            title=title,
            body="Replace with the exact wording your physician gave you.\n",
            authored_by="Dr Example, Endocrinology",
            authored_on=date(2026, 1, 1),
            sort_order=0,
        )
        for category, title in (
            (InstructionCategory.ILLNESS, "Sick day rules"),
            (InstructionCategory.EMERGENCY, "Emergency injection"),
        )
    ]
    session.add_all([*slots, *instructions])
    session.flush()
    if approved:
        medication_service.approve_version(
            session,
            regimen,
            approved_by="Dr Example, Endocrinology",
            approval_source="clinic letter 2026-01-01",
            approved_at=datetime(2026, 1, 1, tzinfo=UTC),
        )
    return {
        "regimen_id": regimen.id,
        "medication_ids": tuple(row.id for row in medications),
        "slot_ids": tuple(row.id for row in slots),
        "instruction_ids": tuple(row.id for row in instructions),
    }


def _a_medication(client: TestClient, headers: dict[str, str]) -> str:
    existing = client.get("/api/v1/medications").json()
    if existing:
        return existing[0]["id"]
    created = client.post(
        "/api/v1/medications",
        json={"name": "Hydrocortisone", "default_unit": "mg", "default_route": "oral"},
        headers=headers,
    )
    return created.json()["id"]


def _a_dose(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    return client.post(
        "/api/v1/doses",
        json={
            "medication_id": _a_medication(client, headers),
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": "2026-05-01T07:00:00", "timezone": "Europe/London"},
        },
        headers=headers,
    ).json()


def _a_draft_regimen(client: TestClient, headers: dict[str, str]) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    response = client.post(
        "/api/v1/regimens",
        json={
            "version_label": f"draft {stamp}",
            "effective_from": f"20{int(stamp[2:4]) % 50 + 30}-01-01T00:00:00",
            "slots": [],
            "instructions": [],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]


def test_common_views_meet_latency_targets_on_six_year_synthetic_volume(
    engine: Engine,
) -> None:
    """Warmed medians stay below the private single-owner UI latency budgets."""

    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        try:
            owner = Owner(
                email=f"performance-{uuid.uuid4()}@example.test",
                password_hash="synthetic-non-login-hash",
                default_timezone="UTC",
            )
            medication = Medication(
                owner_id=owner.id,
                name="Synthetic performance medicine",
                normalized_name=f"synthetic-performance-{uuid.uuid4()}",
                formulation="tablet",
                strength=Decimal("10"),
                strength_unit="mg",
                default_unit=DoseUnit.MG,
                default_route=Route.ORAL,
            )
            session.add(owner)
            session.flush()
            medication.owner_id = owner.id
            session.add(medication)
            session.flush()

            versions: list[RegimenVersion] = []
            for year in range(2020, 2026):
                start = datetime(year, 1, 1, tzinfo=UTC).replace(tzinfo=None)
                end = datetime(year + 1, 1, 1, tzinfo=UTC).replace(tzinfo=None)
                version = medication_service.create_draft(
                    session,
                    owner_id=owner.id,
                    version_label=f"Synthetic performance plan {year}",
                    effective_from=start,
                    effective_to=end,
                )
                for order, scheduled in enumerate((time(7), time(12), time(17), time(22))):
                    session.add(
                        RegimenDoseSlot(
                            regimen_version_id=version.id,
                            medication_id=medication.id,
                            scheduled_local_time=scheduled,
                            amount=Decimal("10") + Decimal(year - 2020) / Decimal("10"),
                            unit=DoseUnit.MG,
                            route=Route.ORAL,
                            sort_order=order,
                        )
                    )
                medication_service.approve_version(
                    session,
                    version,
                    approved_by="Dr Synthetic Performance",
                    approval_source="synthetic performance fixture",
                    approved_at=datetime(year, 1, 1, tzinfo=UTC),
                )
                if year < 2025:
                    medication_service.retire_version(
                        session, version, retired_at=datetime(year + 1, 1, 1, tzinfo=UTC)
                    )
                versions.append(version)
            session.flush()

            start_at = datetime(2020, 1, 1, tzinfo=UTC)
            dose_rows: list[dict[str, Any]] = []
            symptom_rows: list[dict[str, Any]] = []
            diary_rows: list[dict[str, Any]] = []
            for day_offset in range(365 * 6):
                day_start = start_at + timedelta(days=day_offset)
                for hour in (7, 12, 17, 22):
                    occurred = day_start.replace(hour=hour)
                    dose_rows.append(
                        {
                            "id": uuid.uuid4(),
                            "owner_id": owner.id,
                            "occurred_at": occurred,
                            "local_time": occurred.replace(tzinfo=None),
                            "timezone": "UTC",
                            "utc_offset_minutes": 0,
                            "recorded_at": occurred + timedelta(minutes=1),
                            "source_type": SourceType.WEB,
                            "confirmation_state": ConfirmationState.DIRECT,
                            "medication_id": medication.id,
                            "amount": Decimal("10"),
                            "unit": DoseUnit.MG,
                            "route": Route.ORAL,
                            "category": DoseCategory.SCHEDULED,
                        }
                    )
                if day_offset % 3 == 0:
                    symptom_rows.append(
                        {
                            "id": uuid.uuid4(),
                            "owner_id": owner.id,
                            "occurred_at": day_start.replace(hour=18),
                            "local_time": day_start.replace(hour=18, tzinfo=None),
                            "timezone": "UTC",
                            "utc_offset_minutes": 0,
                            "recorded_at": day_start.replace(hour=18, minute=1),
                            "source_type": SourceType.WEB,
                            "confirmation_state": ConfirmationState.DIRECT,
                            "name": "Synthetic performance symptom",
                            "severity": day_offset % 11,
                        }
                    )
                if day_offset % 7 == 0:
                    diary_rows.append(
                        {
                            "id": uuid.uuid4(),
                            "owner_id": owner.id,
                            "occurred_at": day_start.replace(hour=20),
                            "local_time": day_start.replace(hour=20, tzinfo=None),
                            "timezone": "UTC",
                            "utc_offset_minutes": 0,
                            "recorded_at": day_start.replace(hour=20, minute=1),
                            "source_type": SourceType.WEB,
                            "confirmation_state": ConfirmationState.DIRECT,
                            "text": "Synthetic performance diary entry",
                            "is_sensitive": False,
                        }
                    )
            session.execute(insert(DoseEvent), dose_rows)
            session.execute(insert(SymptomEvent), symptom_rows)
            session.execute(insert(DiaryEvent), diary_rows)
            session.flush()

            comparison_day = date(2025, 6, 15)

            def timeline_view() -> object:
                from healthcurve.api.pagination import PageRequest

                session.expire_all()
                return events_router.timeline(
                    session=session,
                    owner=owner,
                    pagination=PageRequest(page=1, page_size=25),
                    date_from=None,
                    date_to=None,
                    types=None,
                    timezone="UTC",
                    local_date_from=None,
                    local_date_to=None,
                    include_sensitive=False,
                    sort_order="desc",
                )

            def today_view() -> object:
                session.expire_all()
                return medication_service.compare_day(
                    session, owner_id=owner.id, day=comparison_day, timezone="UTC"
                )

            def plan_diff_view() -> object:
                session.expire_all()
                older = session.get(RegimenVersion, versions[0].id)
                newer = session.get(RegimenVersion, versions[-1].id)
                assert older is not None and newer is not None
                return medication_service.diff_versions(older, newer)

            def warmed_median(operation: Any) -> float:
                operation()
                samples = []
                for _ in range(7):
                    started = perf_counter()
                    operation()
                    samples.append((perf_counter() - started) * 1000)
                return median(samples)

            timeline_ms = warmed_median(timeline_view)
            today_ms = warmed_median(today_view)
            plan_diff_ms = warmed_median(plan_diff_view)

            assert timeline_ms <= 750, f"Timeline median {timeline_ms:.1f} ms exceeds 750 ms"
            assert today_ms <= 250, f"Today median {today_ms:.1f} ms exceeds 250 ms"
            assert plan_diff_ms <= 100, f"plan diff median {plan_diff_ms:.1f} ms exceeds 100 ms"

            plan = connection.execute(
                text(
                    "EXPLAIN (FORMAT JSON) SELECT id FROM fact.dose_event "
                    "WHERE owner_id = :owner_id ORDER BY occurred_at DESC LIMIT 200"
                ),
                {"owner_id": owner.id},
            ).scalar_one()
            assert "Index" in str(plan), plan
        finally:
            session.close()
            transaction.rollback()


def test_chat_conversation_lifecycle_is_owner_scoped_bounded_and_non_authoritative(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    """Chat persistence is usable but cannot mutate fact or plan categories."""

    def authoritative_signature() -> dict[str, tuple[int, str]]:
        signatures: dict[str, tuple[int, str]] = {}
        with engine.connect() as connection:
            for table in all_models.Base.metadata.sorted_tables:
                if table.schema not in {"fact", "plan"}:
                    continue
                key = f"{table.schema}.{table.name}"
                count, digest = connection.execute(
                    text(
                        f"SELECT count(*), md5(coalesce("
                        f"string_agg(to_jsonb(t)::text, ',' ORDER BY to_jsonb(t)::text), '')) "
                        f'FROM "{table.schema}"."{table.name}" AS t'
                    )
                ).one()
                signatures[key] = (count, digest)
        return signatures

    before = authoritative_signature()
    created = client.post(
        "/api/v1/chat/conversations",
        headers=logged_in,
        json={"title": "Synthetic continuity check", "include_sensitive_text": False},
    )
    assert created.status_code == 201, created.text
    assert created.headers["cache-control"] == "no-store"
    conversation_id = created.json()["id"]
    assert created.json()["category"] == "ai"

    appended = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        headers=logged_in,
        json={"body": "Compare the synthetic dates.", "client_message_id": "browser-1"},
    )
    assert appended.status_code == 202, appended.text
    assert appended.json()["content_category"] == "owner_authored"
    message_id = appended.json()["id"]

    queued_page = client.get(
        f"/api/v1/chat/conversations/{conversation_id}/messages?page=1&page_size=5"
    )
    assert queued_page.status_code == 200
    assert [(item["role"], item["state"]) for item in queued_page.json()["items"]] == [
        ("user", "accepted"),
        ("assistant", "queued"),
    ]
    assistant_id = queued_page.json()["items"][1]["id"]
    staleness = client.get(f"/api/v1/chat/messages/{assistant_id}/staleness")
    assert staleness.status_code == 200, staleness.text
    assert staleness.json()["status"] == "not_applicable"
    assert staleness.json()["stale"] is None

    duplicate = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        headers=logged_in,
        json={"body": "Compare the synthetic dates.", "client_message_id": "browser-1"},
    )
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == message_id
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count()).select_from(Job).where(Job.task == CHAT_RESPONSE_TASK)
            )
            == 1
        )
    conflict = client.post(
        f"/api/v1/chat/conversations/{conversation_id}/messages",
        headers=logged_in,
        json={"body": "Different content.", "client_message_id": "browser-1"},
    )
    assert conflict.status_code == 409

    cancelled = client.post(
        f"/api/v1/chat/messages/{assistant_id}/cancel",
        headers=logged_in,
    )
    assert cancelled.status_code == 200, cancelled.text
    assert cancelled.json()["state"] == "cancelled"
    assert (
        client.post(f"/api/v1/chat/messages/{assistant_id}/cancel", headers=logged_in).status_code
        == 409
    )

    renamed = client.patch(
        f"/api/v1/chat/conversations/{conversation_id}",
        headers=logged_in,
        json={"title": "Renamed synthetic conversation", "include_sensitive_text": True},
    )
    assert renamed.status_code == 200
    assert renamed.json()["title"] == "Renamed synthetic conversation"
    assert renamed.json()["include_sensitive_text"] is True

    with Session(engine) as session, session.begin():
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        other_owner = Owner(
            email=f"chat-other-{uuid.uuid4()}@example.test",
            password_hash=auth.hash_password("synthetic-other-password"),
            default_timezone="UTC",
        )
        session.add(other_owner)
        session.flush()
        other_conversation = ChatConversation(owner_id=other_owner.id, title="Other owner")
        session.add(other_conversation)
        session.flush()
        other_id = other_conversation.id

        conversation_uuid = uuid.UUID(conversation_id)
        last_sequence = (
            session.scalar(
                select(func.max(ChatMessage.sequence)).where(
                    ChatMessage.conversation_id == conversation_uuid
                )
            )
            or 0
        )
        for index in range(13):
            session.add(
                ChatMessage(
                    conversation_id=conversation_uuid,
                    owner_id=owner_id,
                    role=ChatRole.USER,
                    state=ChatMessageState.ACCEPTED,
                    body=f"turn-{index}:" + ("x" * 2490),
                    sequence=last_sequence + index + 1,
                    client_message_id=f"bounded-{index}",
                )
            )
        session.flush()
        bounded = chat_service.bounded_context(
            session, owner_id=owner_id, conversation_id=conversation_uuid
        )
        assert len(bounded.turns) <= chat_service.MAX_CONTEXT_TURNS
        assert bounded.character_count <= chat_service.MAX_CONTEXT_CHARS
        assert [turn.sequence for turn in bounded.turns] == sorted(
            turn.sequence for turn in bounded.turns
        )

    assert client.get(f"/api/v1/chat/conversations/{other_id}").status_code == 404
    page = client.get(f"/api/v1/chat/conversations/{conversation_id}/messages?page=1&page_size=5")
    assert page.status_code == 200
    assert page.headers["cache-control"] == "no-store"
    assert page.json()["page"]["total_items"] == 15
    assert [item["sequence"] for item in page.json()["items"]] == [1, 2, 3, 4, 5]

    deleted = client.delete(f"/api/v1/chat/conversations/{conversation_id}", headers=logged_in)
    assert deleted.status_code == 204
    assert deleted.headers["cache-control"] == "no-store"
    assert client.get(f"/api/v1/chat/conversations/{conversation_id}").status_code == 404
    assert authoritative_signature() == before
    with Session(engine) as session:
        actions = set(
            session.scalars(
                select(AuditEntry.action).where(
                    AuditEntry.target_id.in_((uuid.UUID(conversation_id), uuid.UUID(message_id)))
                )
            )
        )
        assert AuditAction.CHAT_CONVERSATION_CREATED in actions
        assert AuditAction.CHAT_MESSAGE_ACCEPTED in actions
        assert AuditAction.CHAT_CONVERSATION_DELETED in actions
        audit_text = " ".join(
            summary or ""
            for summary in session.scalars(
                select(AuditEntry.change_summary).where(
                    AuditEntry.target_id.in_((uuid.UUID(conversation_id), uuid.UUID(message_id)))
                )
            )
        )
        assert "Compare the synthetic dates" not in audit_text

    for title in ("First synthetic thread", "Second synthetic thread"):
        response = client.post(
            "/api/v1/chat/conversations", headers=logged_in, json={"title": title}
        )
        assert response.status_code == 201
    first_page = client.get("/api/v1/chat/conversations?page=1&page_size=1")
    assert first_page.status_code == 200
    assert first_page.json()["page"]["total_items"] == 2
    assert len(first_page.json()["items"]) == 1
    deleted_all = client.delete("/api/v1/chat/conversations", headers=logged_in)
    assert deleted_all.status_code == 204
    assert client.get("/api/v1/chat/conversations").json()["page"]["total_items"] == 0
    with Session(engine) as session:
        assert session.get(ChatConversation, other_id) is not None
    assert authoritative_signature() == before


@pytest.mark.safety("SAFE-05")
def test_database_rejects_completed_chat_answer_without_full_provenance(engine: Engine) -> None:
    with Session(engine) as session:
        owner_id = session.scalar(select(Owner.id).where(Owner.email == EMAIL))
        assert owner_id is not None
        conversation = ChatConversation(owner_id=owner_id, title="Synthetic provenance check")
        session.add(conversation)
        session.flush()
        session.add(
            ChatMessage(
                conversation_id=conversation.id,
                owner_id=owner_id,
                role=ChatRole.ASSISTANT,
                state=ChatMessageState.COMPLETED,
                body="Synthetic answer that must not persist without provenance.",
                sequence=1,
            )
        )
        with pytest.raises(IntegrityError):
            session.commit()
