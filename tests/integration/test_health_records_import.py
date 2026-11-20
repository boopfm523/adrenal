"""Importing the external health-records dataset into real PostgreSQL (ADR-0040).

Runs the production migrations and role provisioning, so the privilege and view
boundaries under test are the ones production uses. All data is synthetic.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.api.pagination import PageRequest
from healthcurve.api.routers.labs import list_lab_results
from healthcurve.config import get_settings
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.timekeeping import resolve_event_time
from healthcurve.identity.models import Owner
from healthcurve.integrations.health_records import visibility
from healthcurve.integrations.health_records.models import (
    AiVisibilityCategory,
    DatasetTable,
    HealthRecord,
    HealthRecordImportBatch,
    HealthRecordRevision,
    RecordState,
)
from healthcurve.integrations.health_records.reader import read_snapshot
from healthcurve.integrations.health_records.service import import_snapshot
from healthcurve.labs.models import LabPanel, LabResult
from healthcurve.labs.service import create_panel, manual_candidate
from healthcurve.medications.models import Medication
from healthcurve.privacy import delete_account
from tests.fixtures.health_records_dataset import default_tables, write_dataset

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
INIT_DIR = REPO_ROOT / "deploy" / "postgres-init"
ZONE = "America/New_York"

OWNER_PASSWORD = "owner-test-password"  # pragma: allowlist secret - ephemeral container
AI_PASSWORD = "ai-test-password"  # pragma: allowlist secret - ephemeral container
BACKUP_PASSWORD = "backup-test-password"  # pragma: allowlist secret - ephemeral container
ANALYST_PASSWORD = "analyst-test-password"  # pragma: allowlist secret - ephemeral container
ANALYST_TEXT_PASSWORD = "analyst-text-test-password"  # pragma: allowlist secret - ephemeral


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
def engine(postgres: PostgresContainer) -> Iterator[Engine]:
    created = create_engine(postgres.get_connection_url())
    yield created
    created.dispose()


@pytest.fixture(scope="module")
def analyst_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    created = _role_engine(postgres, "healthcurve_analyst", ANALYST_PASSWORD)
    yield created
    created.dispose()


@pytest.fixture(scope="module")
def ai_engine(postgres: PostgresContainer) -> Iterator[Engine]:
    created = _role_engine(postgres, "healthcurve_ai", AI_PASSWORD)
    yield created
    created.dispose()


def _new_owner(engine: Engine) -> uuid.UUID:
    with Session(engine) as session, session.begin():
        owner = Owner(
            email=f"health-records-{uuid.uuid4()}@example.test",
            password_hash="synthetic-not-a-login-hash",
            default_timezone=ZONE,
        )
        session.add(owner)
        session.flush()
        return owner.id


def _import(engine: Engine, owner_id: uuid.UUID, root: Path, *, dry_run: bool = False):
    snapshot = read_snapshot(root)
    with Session(engine) as session, session.begin():
        result = import_snapshot(session, owner_id=owner_id, snapshot=snapshot, dry_run=dry_run)
        if dry_run:
            session.rollback()
        return result


def _current_imported_panels(session: Session, owner_id: uuid.UUID) -> list[LabPanel]:
    superseded = select(LabPanel.supersedes_id).where(LabPanel.supersedes_id.is_not(None))
    return list(
        session.scalars(
            select(LabPanel).where(
                LabPanel.owner_id == owner_id,
                LabPanel.source_type == SourceType.HEALTH_RECORDS_IMPORT,
                LabPanel.id.not_in(superseded),
            )
        )
    )


def _envelope(session: Session, owner_id: uuid.UUID, record_id: str) -> HealthRecord:
    return session.scalars(
        select(HealthRecord).where(
            HealthRecord.owner_id == owner_id, HealthRecord.record_id == record_id
        )
    ).one()


def test_first_import_creates_records_batches_and_lab_panels(
    engine: Engine, tmp_path: Path
) -> None:
    owner_id = _new_owner(engine)
    result = _import(engine, owner_id, write_dataset(tmp_path))
    assert result.tables[DatasetTable.LAB_RESULTS].new == 7
    assert result.tables[DatasetTable.DOCUMENTS].new == 2
    assert result.labs.panels_created == 5
    assert result.labs.results_without_value == 1

    with Session(engine) as session:
        batch = session.get(HealthRecordImportBatch, result.batch_id)
        assert batch is not None
        assert batch.dataset_generated_at == "2025-06-01T12:00:00"
        assert batch.source_exported_at["NIH Clinical Center"] == "2025-05-30"
        assert batch.counts["lab_results"]["new"] == 7

        panels = {p.panel_name: p for p in _current_imported_panels(session, owner_id)}
        mayo = panels["CORTISOL AM"]
        assert mayo.timezone == "America/Chicago"
        assert mayo.local_time == datetime(2025, 3, 10, 9, 0)  # noqa: DTZ001
        assert mayo.occurred_at == datetime(2025, 3, 10, 14, 0, tzinfo=UTC)
        assert mayo.reported_at == mayo.occurred_at
        assert mayo.source_organization == "Mayo Clinic"
        assert mayo.confirmation_state is ConfirmationState.PROVIDER_IMPORTED
        assert panels["ACTH"].timezone == "America/New_York"
        cortisol = mayo.results[0]
        assert cortisol.analyte_name == "Cortisol, Serum"
        assert cortisol.normalized_analyte_code == "cortisol"
        assert cortisol.normalized_unit == "nmol/L"

        urine = panels["URINALYSIS"].results[0]
        assert urine.source_normalized_name == "Glucose (urine)"
        assert urine.normalized_analyte_code is None
        assert urine.qualitative_result == "Negative"

        nih = panels["CBC"]
        assert {r.normalized_analyte_code for r in nih.results} == {"hemoglobin", "wbc"}

        medication = _envelope(session, owner_id, "med_syn_001")
        assert medication.ai_category is None
        assert (
            session.scalar(
                select(func.count()).select_from(Medication).where(Medication.owner_id == owner_id)
            )
            == 0
        )


def test_reimporting_the_same_snapshot_changes_nothing(engine: Engine, tmp_path: Path) -> None:
    owner_id = _new_owner(engine)
    root = write_dataset(tmp_path)
    _import(engine, owner_id, root)
    second = _import(engine, owner_id, root)
    assert all(c.new == c.changed == c.no_longer_in_source == 0 for c in second.tables.values())
    assert second.labs.panels_created == second.labs.panels_corrected == 0
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(HealthRecordImportBatch)
                .where(HealthRecordImportBatch.owner_id == owner_id)
            )
            == 2
        )
        assert session.scalar(
            select(func.count())
            .select_from(HealthRecordRevision)
            .where(HealthRecordRevision.owner_id == owner_id)
        ) == sum(len(rows) for rows in default_tables().values())


def test_dry_run_writes_nothing(engine: Engine, tmp_path: Path) -> None:
    owner_id = _new_owner(engine)
    result = _import(engine, owner_id, write_dataset(tmp_path), dry_run=True)
    assert result.batch_id is None
    assert result.tables[DatasetTable.LAB_RESULTS].new == 7
    with Session(engine) as session:
        assert (
            session.scalar(
                select(func.count())
                .select_from(HealthRecord)
                .where(HealthRecord.owner_id == owner_id)
            )
            == 0
        )


def test_corrections_append_revisions_and_supersede_panels(engine: Engine, tmp_path: Path) -> None:
    owner_id = _new_owner(engine)
    tables = default_tables()
    _import(engine, owner_id, write_dataset(tmp_path, tables=tables))
    tables["lab_results"][0]["value_text"] = "14.1"
    tables["lab_results"][0]["value_num"] = 14.1
    second = _import(engine, owner_id, write_dataset(tmp_path, tables=tables))
    assert second.tables[DatasetTable.LAB_RESULTS].changed == 1
    assert second.labs.panels_corrected == 1

    with Session(engine) as session:
        envelope = _envelope(session, owner_id, "lab_syn_001")
        assert envelope.current_revision == 2
        cbc_all = list(
            session.scalars(
                select(LabPanel).where(LabPanel.owner_id == owner_id, LabPanel.panel_name == "CBC")
            )
        )
        assert len(cbc_all) == 2  # the earlier version is retained as history
        current = next(
            p for p in _current_imported_panels(session, owner_id) if p.panel_name == "CBC"
        )
        assert current.supersedes_id is not None
        assert {r.original_value for r in current.results} == {"14.1", "6.1"}

        owner = session.get(Owner, owner_id)
        assert owner is not None
        page = list_lab_results(session, owner, PageRequest(page=1, page_size=100))
        hemoglobin = [item for item in page.items if item.analyte_name == "HGB"]
        assert [item.original_value for item in hemoglobin] == ["14.1"]
        assert hemoglobin[0].source_organization == "NIH Clinical Center"
        assert hemoglobin[0].source_state == "present"


def test_missing_records_are_marked_never_deleted_and_can_return(
    engine: Engine, tmp_path: Path
) -> None:
    owner_id = _new_owner(engine)
    tables = default_tables()
    root = write_dataset(tmp_path, tables=tables)
    _import(engine, owner_id, root)
    removed = tables["lab_results"].pop(1)
    tables["documents"].pop(0)
    second = _import(engine, owner_id, write_dataset(tmp_path, tables=tables))
    assert second.tables[DatasetTable.LAB_RESULTS].no_longer_in_source == 1
    assert second.tables[DatasetTable.DOCUMENTS].no_longer_in_source == 1
    # A withdrawal is a state change, not a panel content change.
    assert second.labs.panels_corrected == 0
    with Session(engine) as session:
        envelope = _envelope(session, owner_id, "lab_syn_002")
        assert envelope.state is RecordState.NO_LONGER_IN_SOURCE
        assert envelope.missing_since_batch_id == second.batch_id
        owner = session.get(Owner, owner_id)
        assert owner is not None
        page = list_lab_results(session, owner, PageRequest(page=1, page_size=100))
        wbc = next(item for item in page.items if item.analyte_name == "WBC")
        assert wbc.source_state == "no_longer_in_source"

    tables["lab_results"].insert(1, removed)
    third = _import(engine, owner_id, write_dataset(tmp_path, tables=tables))
    assert third.tables[DatasetTable.LAB_RESULTS].returned == 1
    with Session(engine) as session:
        envelope = _envelope(session, owner_id, "lab_syn_002")
        assert envelope.state is RecordState.PRESENT
        assert envelope.missing_since_batch_id is None


def _analytics_lab_names(analyst_engine: Engine, owner_id: uuid.UUID, engine: Engine) -> set[str]:
    with Session(engine) as session:
        result_ids = [
            str(result_id)
            for result_id in session.scalars(
                select(LabResult.id).where(LabResult.owner_id == owner_id)
            )
        ]
    if not result_ids:
        return set()
    with analyst_engine.connect() as connection:
        return set(
            connection.execute(
                text(
                    "SELECT analyte_name FROM analytics.lab_results "
                    "WHERE result_id = ANY(CAST(:ids AS uuid[]))"
                ),
                {"ids": result_ids},
            ).scalars()
        )


def test_imported_labs_are_hidden_from_chat_views_until_opted_in(
    engine: Engine, analyst_engine: Engine, tmp_path: Path
) -> None:
    owner_id = _new_owner(engine)
    with Session(engine) as session, session.begin():
        candidate = manual_candidate(
            analyte_name="Sodium",
            original_value="140",
            qualitative_result=None,
            original_unit="mmol/L",
            original_reference_range=None,
            abnormal_flag=None,
        )
        at = resolve_event_time(datetime(2025, 1, 2, 8, 0), ZONE)  # noqa: DTZ001
        create_panel(
            session,
            owner_id=owner_id,
            specimen_time=at,
            report_time=at,
            candidates=[candidate],
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            laboratory_name="Owner-entered synthetic lab",
        )
    tables = default_tables()
    _import(engine, owner_id, write_dataset(tmp_path, tables=tables))

    assert _analytics_lab_names(analyst_engine, owner_id, engine) == {"Sodium"}

    with Session(engine) as session, session.begin():
        assert visibility.set_category(
            session, owner_id=owner_id, category=AiVisibilityCategory.LABS, enabled=True
        )
    visible = _analytics_lab_names(analyst_engine, owner_id, engine)
    assert {"HGB", "WBC", "Cortisol, Serum", "Glucose"} <= visible

    tables["lab_results"] = [r for r in tables["lab_results"] if r["record_id"] != "lab_syn_002"]
    _import(engine, owner_id, write_dataset(tmp_path, tables=tables))
    assert "WBC" not in _analytics_lab_names(analyst_engine, owner_id, engine)

    with Session(engine) as session, session.begin():
        visibility.set_category(
            session, owner_id=owner_id, category=AiVisibilityCategory.LABS, enabled=False
        )
    assert _analytics_lab_names(analyst_engine, owner_id, engine) == {"Sodium"}


@pytest.mark.parametrize(
    "table",
    ["fact.health_record", "fact.health_record_revision", "fact.health_record_import_batch"],
)
def test_analyst_and_ai_roles_cannot_read_imported_record_tables(
    analyst_engine: Engine, ai_engine: Engine, table: str
) -> None:
    for role_engine in (analyst_engine, ai_engine):
        with role_engine.connect() as connection, pytest.raises(DBAPIError):
            connection.execute(text(f"SELECT 1 FROM {table} LIMIT 1"))


def test_ai_role_can_read_but_never_change_visibility(engine: Engine, ai_engine: Engine) -> None:
    owner_id = _new_owner(engine)
    with ai_engine.connect() as connection:
        connection.execute(text("SELECT count(*) FROM ops.health_record_ai_visibility"))
    with ai_engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "INSERT INTO ops.health_record_ai_visibility (owner_id, category, enabled) "
                "VALUES (:owner, 'labs', true)"
            ),
            {"owner": owner_id},
        )


def test_medication_rows_can_never_carry_an_ai_category(engine: Engine, tmp_path: Path) -> None:
    owner_id = _new_owner(engine)
    _import(engine, owner_id, write_dataset(tmp_path))
    with engine.begin() as connection, pytest.raises(DBAPIError):
        connection.execute(
            text(
                "UPDATE fact.health_record SET ai_category = 'labs' "
                "WHERE owner_id = :owner AND dataset_table = 'medications'"
            ),
            {"owner": owner_id},
        )


def test_account_deletion_removes_imported_records_and_panel_chains(
    engine: Engine, tmp_path: Path
) -> None:
    owner_id = _new_owner(engine)
    tables = default_tables()
    _import(engine, owner_id, write_dataset(tmp_path, tables=tables))
    tables["lab_results"][0]["value_text"] = "15.0"
    _import(engine, owner_id, write_dataset(tmp_path, tables=tables))
    with Session(engine) as session, session.begin():
        owner = session.get(Owner, owner_id)
        assert owner is not None
        delete_account(
            session, owner=owner, uploads_dir=tmp_path / "uploads", telegram_chat_id=None
        )
    with Session(engine) as session:
        for model in (HealthRecord, HealthRecordRevision, HealthRecordImportBatch, LabPanel):
            assert (
                session.scalar(
                    select(func.count()).select_from(model).where(model.owner_id == owner_id)
                )
                == 0
            )


def test_decimal_values_survive_import_exactly(engine: Engine, tmp_path: Path) -> None:
    owner_id = _new_owner(engine)
    _import(engine, owner_id, write_dataset(tmp_path))
    with Session(engine) as session:
        sodium = session.scalars(
            select(LabResult).where(
                LabResult.owner_id == owner_id, LabResult.analyte_name == "Sodium"
            )
        ).one()
        assert sodium.original_value == "139"
        assert sodium.normalized_value == Decimal("139.0000000000")


def test_migration_downgrades_and_upgrades_cleanly() -> None:
    """Round-trip in a fresh container so the shared module database is untouched."""
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
        url = running.get_connection_url()
        try:
            with mock.patch.dict(os.environ, {"HC_DATABASE_URL": url}):
                get_settings.cache_clear()
                command.upgrade(_alembic_config(), "head")
                command.downgrade(_alembic_config(), "e4b7c1a9d305")
                probe = create_engine(url)
                with probe.connect() as connection:
                    assert (
                        connection.execute(
                            text("SELECT to_regclass('fact.health_record')")
                        ).scalar()
                        is None
                    )
                    definition = connection.execute(
                        text("SELECT pg_get_viewdef('analytics.lab_results')")
                    ).scalar_one()
                    assert "health_record" not in definition
                probe.dispose()
                command.upgrade(_alembic_config(), "head")
        finally:
            get_settings.cache_clear()


def test_day_analysis_input_excludes_imported_labs_until_opted_in(
    engine: Engine, tmp_path: Path
) -> None:
    from datetime import date as _date

    from healthcurve.analytics import day_analysis

    owner_id = _new_owner(engine)
    _import(engine, owner_id, write_dataset(tmp_path))

    def lab_count() -> int:
        with Session(engine) as session:
            projection = day_analysis.build_projection(
                session, owner_id=owner_id, day=_date(2025, 1, 15), timezone=ZONE
            )
        counts = projection["data_availability_counts"]
        assert isinstance(counts, dict)
        return int(counts["labs"])  # pyright: ignore[reportUnknownArgumentType]

    assert lab_count() == 0
    with Session(engine) as session, session.begin():
        visibility.set_category(
            session, owner_id=owner_id, category=AiVisibilityCategory.LABS, enabled=True
        )
    assert lab_count() == 1


def test_organization_filter_uses_an_opaque_per_owner_key(engine: Engine, tmp_path: Path) -> None:
    from healthcurve.api.routers.health_records import (
        health_record_organizations,
        list_health_records,
    )

    owner_id = _new_owner(engine)
    _import(engine, owner_id, write_dataset(tmp_path))
    with Session(engine) as session:
        owner = session.get(Owner, owner_id)
        assert owner is not None
        organizations = health_record_organizations(session, owner)
        mayo = next(item for item in organizations if item.name == "Mayo Clinic")
        assert "mayo" not in mayo.key.lower() and len(mayo.key) == 16
        page = list_health_records(
            session,
            owner,
            PageRequest(page=1, page_size=100),
            dataset_table=DatasetTable.LAB_RESULTS,
            organization_key=mayo.key,
        )
        assert {item.organization for item in page.items} == {"Mayo Clinic"}
        assert page.page.total_items == 1
        other_owner = _new_owner(engine)
        assert all(
            item.key != mayo.key
            for item in health_record_organizations(session, session.get(Owner, other_owner))  # type: ignore[arg-type]
        )


def test_lab_test_index_history_and_filter_cover_every_result(
    engine: Engine, tmp_path: Path
) -> None:
    from healthcurve.api.routers.labs import lab_test_history, lab_test_index

    owner_id = _new_owner(engine)
    _import(engine, owner_id, write_dataset(tmp_path))
    with Session(engine) as session:
        owner = session.get(Owner, owner_id)
        assert owner is not None
        tests = {item.name: item for item in lab_test_index(session, owner)}
        # Registry analytes use their display name; others keep the dataset name.
        assert {"Hemoglobin", "Cortisol", "ACTH", "Glucose (urine)"} <= set(tests)
        assert all("acth" not in item.key.lower() for item in tests.values())
        assert sum(item.result_count for item in tests.values()) == 6

        acth = lab_test_history(tests["ACTH"].key, session, owner)
        assert [item.analyte_name for item in acth] == ["ACTH"]
        assert acth[0].source_organization == ("University of Maryland Medical System (UMMS)")
        page = list_lab_results(
            session, owner, PageRequest(page=1, page_size=100), test_key=tests["Cortisol"].key
        )
        assert [item.analyte_name for item in page.items] == ["Cortisol, Serum"]


def test_import_endpoint_dry_run_then_import(engine: Engine, tmp_path: Path) -> None:
    from fastapi import HTTPException, Response

    from healthcurve.api.routers.health_records import run_health_record_import
    from healthcurve.operations.rate_limit import RateLimiter

    owner_id = _new_owner(engine)
    root = write_dataset(tmp_path)
    limiter = RateLimiter(None)
    configured = get_settings().model_copy(update={"health_records_root": root})
    with Session(engine) as session:
        owner = session.get(Owner, owner_id)
        assert owner is not None
        preview = run_health_record_import(
            Response(), session, owner, configured, limiter, dry_run=True
        )
        assert preview.batch_id is None
        assert next(row for row in preview.tables if row.table is DatasetTable.LAB_RESULTS).new == 7
        assert (
            session.scalar(
                select(func.count())
                .select_from(HealthRecord)
                .where(HealthRecord.owner_id == owner_id)
            )
            == 0
        )

        done = run_health_record_import(Response(), session, owner, configured, limiter)
        assert done.batch_id is not None
        assert done.lab_panels_created == 5

        unconfigured = get_settings().model_copy(update={"health_records_root": None})
        with pytest.raises(HTTPException) as missing:
            run_health_record_import(Response(), session, owner, unconfigured, limiter)
        assert missing.value.status_code == 409

        broken = tmp_path / "broken"
        write_dataset(broken, meta={"schema_version": "3", "generated_at": "x"})
        refused = get_settings().model_copy(update={"health_records_root": broken})
        with pytest.raises(HTTPException) as unsupported:
            run_health_record_import(Response(), session, owner, refused, limiter)
        assert unsupported.value.status_code == 409
        assert "schema_version 3" in str(unsupported.value.detail)
