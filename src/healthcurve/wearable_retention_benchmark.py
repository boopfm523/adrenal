"""Disposable multi-year wearable backup and isolated-restore measurement."""

from __future__ import annotations

import os
import secrets
import tempfile
from collections.abc import Generator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import perf_counter
from typing import Any
from unittest import mock

from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import Session

from healthcurve.config import get_settings
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.models import (
    GarminSyncOrigin,
    GarminSyncRun,
    GarminSyncStatus,
)
from healthcurve.operations.restore_drill import assert_restore_sentinel
from healthcurve.wearable_benchmark import ScalePlan, build_scale_plan, seed_metric_rows

# pragma: allowlist secret - pinned public container digest
POSTGRES_IMAGE = "postgres@sha256:57c72fd2a128e416c7fcc499958864df5301e940bca0a56f58fddf30ffc07777"


class RetentionBenchmarkError(RuntimeError):
    """A safe reason code for a disposable benchmark failure."""


def result_skeleton(scale: ScalePlan) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "fixture": "synthetic_disposable_multi_year",
        "years": scale.years,
        "days": scale.days,
        "expected_metric_rows": scale.total_metric_rows,
        "backup": {},
        "restore": {},
        "verification": {},
    }


def _migrate(database_url: str, repo_root: Path) -> None:
    config = Config(str(repo_root / "alembic.ini"))
    config.set_main_option("script_location", str(repo_root / "migrations"))
    with mock.patch.dict(os.environ, {"HC_DATABASE_URL": database_url}):
        get_settings.cache_clear()
        command.upgrade(config, "head")
    get_settings.cache_clear()


def _seed(engine: Engine, scale: ScalePlan) -> None:
    with Session(engine) as session, session.begin():
        if session.scalar(text("SELECT count(*) FROM identity.owner")):
            raise RetentionBenchmarkError("retention_benchmark_database_not_empty")
        owner = Owner(
            email=f"retention-benchmark-{secrets.token_hex(8)}@example.test",
            password_hash="synthetic-non-login-hash",  # noqa: S106  # pragma: allowlist secret
            default_timezone="UTC",
        )
        session.add(owner)
        session.flush()
        sync = GarminSyncRun(
            owner_id=owner.id,
            requested_start_date=scale.start_date,
            requested_end_date=scale.end_date_exclusive - timedelta(days=1),
            timezone="UTC",
            origin=GarminSyncOrigin.MANUAL,
            status=GarminSyncStatus.COMPLETED,
            started_at=datetime.now(UTC),
            finished_at=datetime.now(UTC),
            counts={},
            warning_codes=[],
            client_version="synthetic-retention-benchmark-v1",
        )
        session.add(sync)
        session.flush()
        seed_metric_rows(session.connection(), owner_id=owner.id, sync_run_id=sync.id, scale=scale)
    with engine.begin() as connection:
        connection.execute(text("ANALYZE fact.garmin_metric_event"))


def _signature(engine: Engine) -> tuple[int, int]:
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT count(*), coalesce(bit_xor(hashtextextended("
                "row_to_json(metric)::text, 0)), 0) "
                "FROM fact.garmin_metric_event AS metric"
            )
        ).one()
        return int(row[0]), int(row[1])


def _relation_bytes(engine: Engine) -> int:
    with engine.connect() as connection:
        return int(
            connection.scalar(text("SELECT pg_total_relation_size('fact.garmin_metric_event')"))
            or 0
        )


def _exec(container: Any, command_args: list[str], *, environment: dict[str, str]) -> None:
    result = container.get_wrapped_container().exec_run(
        command_args, user="root", environment=environment
    )
    if result.exit_code != 0:
        raise RetentionBenchmarkError("retention_benchmark_database_command_failed")


@contextmanager
def _postgres_container(
    *, password: str, ai_password: str, backup_password: str, shared: Path, init_dir: Path
) -> Generator[Any]:
    from testcontainers.community.postgres import PostgresContainer

    container = (
        PostgresContainer(
            POSTGRES_IMAGE,
            username="healthcurve",
            password=password,
            dbname="healthcurve",
            driver="psycopg",
        )
        .with_env("POSTGRES_AI_PASSWORD", ai_password)
        .with_env("POSTGRES_BACKUP_PASSWORD", backup_password)
        .with_volume_mapping(str(init_dir), "/docker-entrypoint-initdb.d", "ro")
        .with_volume_mapping(str(shared), "/benchmark", "rw")
    )
    with container as running:
        yield running


def run(*, years: int, repo_root: Path) -> dict[str, Any]:
    """Create, dump, and restore only disposable synthetic databases."""
    scale = build_scale_plan(years=years)
    result = result_skeleton(scale)
    owner_password = secrets.token_urlsafe(24)
    ai_password = secrets.token_urlsafe(24)
    backup_password = secrets.token_urlsafe(24)
    init_dir = (repo_root / "deploy" / "postgres-init").resolve()
    with tempfile.TemporaryDirectory(prefix="hc-retention-benchmark-") as temporary:
        shared = Path(temporary).resolve()
        with _postgres_container(
            password=owner_password,
            ai_password=ai_password,
            backup_password=backup_password,
            shared=shared,
            init_dir=init_dir,
        ) as source:
            source_engine = create_engine(source.get_connection_url())
            try:
                _migrate(source.get_connection_url(), repo_root)
                seed_started = perf_counter()
                _seed(source_engine, scale)
                seed_seconds = perf_counter() - seed_started
                source_signature = _signature(source_engine)
                source_relation_bytes = _relation_bytes(source_engine)
                dump_started = perf_counter()
                _exec(
                    source,
                    [
                        "pg_dump",
                        "--format=custom",
                        "--no-owner",
                        "--file=/benchmark/database.dump",
                        "--username=healthcurve_backup",
                        "--dbname=healthcurve",
                    ],
                    environment={"PGPASSWORD": backup_password},
                )
                dump_seconds = perf_counter() - dump_started
                dump = shared / "database.dump"
                if not dump.is_file() or dump.stat().st_size <= 0:
                    raise RetentionBenchmarkError("retention_benchmark_dump_missing")
                result["backup"] = {
                    "seed_seconds": round(seed_seconds, 3),
                    "pg_dump_seconds": round(dump_seconds, 3),
                    "custom_dump_bytes": dump.stat().st_size,
                    "source_relation_bytes": source_relation_bytes,
                }
                with _postgres_container(
                    password=owner_password,
                    ai_password=ai_password,
                    backup_password=backup_password,
                    shared=shared,
                    init_dir=init_dir,
                ) as restored:
                    restore_started = perf_counter()
                    _exec(
                        restored,
                        [
                            "pg_restore",
                            "--exit-on-error",
                            "--clean",
                            "--if-exists",
                            "--no-owner",
                            "--username=healthcurve",
                            "--dbname=healthcurve",
                            "/benchmark/database.dump",
                        ],
                        environment={"PGPASSWORD": owner_password},
                    )
                    restore_seconds = perf_counter() - restore_started
                    restored_engine = create_engine(restored.get_connection_url())
                    try:
                        restored_signature = _signature(restored_engine)
                        assert_restore_sentinel(restored_engine)
                        restored_relation_bytes = _relation_bytes(restored_engine)
                    finally:
                        restored_engine.dispose()
                    result["restore"] = {
                        "pg_restore_seconds": round(restore_seconds, 3),
                        "restored_relation_bytes": restored_relation_bytes,
                    }
                    result["verification"] = {
                        "row_count": restored_signature[0],
                        "expected_row_count_match": restored_signature[0]
                        == scale.total_metric_rows,
                        "exact_fixture_signature_match": restored_signature == source_signature,
                        "restore_sentinel_match": True,
                    }
            finally:
                source_engine.dispose()
    if not all(result["verification"].values()):
        raise RetentionBenchmarkError("retention_benchmark_restore_verification_failed")
    result["generated_at"] = datetime.now(UTC).isoformat()
    return result
