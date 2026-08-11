"""One-shot validation of a decrypted backup against an isolated PostgreSQL stack."""

from __future__ import annotations

import json
import os
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import DBAPIError, SQLAlchemyError

from healthcurve.operations.backup import REQUIRED_SCHEMAS, BackupError
from healthcurve.operations.restore import validated_restore_payload
from healthcurve.operations.restore_sentinel import (
    RESTORE_SENTINEL_ID,
    expected_restore_sentinel,
)


@dataclass(frozen=True)
class DrillSettings:
    envelope: Path
    identity: Path
    work_root: Path
    database_url: str
    ai_database_url: str
    pg_restore: str = "pg_restore"

    @classmethod
    def from_env(cls) -> DrillSettings:
        required = {
            "envelope": os.environ.get("HC_RESTORE_DRILL_ENVELOPE", "").strip(),
            "identity": os.environ.get("HC_RESTORE_DRILL_IDENTITY", "").strip(),
            "work_root": os.environ.get("HC_RESTORE_DRILL_WORK_ROOT", "").strip(),
            "database_url": os.environ.get("HC_RESTORE_DATABASE_URL", "").strip(),
            "ai_database_url": os.environ.get("HC_RESTORE_AI_DATABASE_URL", "").strip(),
        }
        if any(not value for value in required.values()):
            raise BackupError("restore_drill_configuration_incomplete")
        paths = tuple(Path(required[key]) for key in ("envelope", "identity", "work_root"))
        if any(not path.is_absolute() for path in paths):
            raise BackupError("restore_drill_path_not_absolute")
        for url in (required["database_url"], required["ai_database_url"]):
            if not url.startswith("postgresql+psycopg://") or "@restore-postgres:" not in url:
                raise BackupError("restore_drill_database_not_isolated")
        return cls(
            envelope=paths[0],
            identity=paths[1],
            work_root=paths[2],
            database_url=required["database_url"],
            ai_database_url=required["ai_database_url"],
        )


def _restore_database(dump: Path, executable: str) -> None:
    args = (
        executable,
        "--exit-on-error",
        "--clean",
        "--if-exists",
        "--no-owner",
        "--dbname=healthcurve",
        str(dump),
    )
    try:
        result = subprocess.run(  # noqa: S603 - fixed pg_restore argv, no shell
            args,
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise BackupError("restore_database_command_failed") from exc
    if result.returncode != 0:
        raise BackupError("restore_database_failed")


def _assert_database_structure(engine: Engine) -> None:
    try:
        with engine.connect() as connection:
            schemas = set(
                connection.scalars(
                    text(
                        "SELECT schema_name FROM information_schema.schemata "
                        "WHERE schema_name = ANY(:schemas)"
                    ),
                    {"schemas": list(REQUIRED_SCHEMAS)},
                )
            )
            if schemas != set(REQUIRED_SCHEMAS):
                raise BackupError("restore_database_schema_missing")
            extensions = set(
                connection.scalars(
                    text(
                        "SELECT extname FROM pg_extension "
                        "WHERE extname IN ('btree_gist','pgcrypto')"
                    )
                )
            )
            if extensions != {"btree_gist", "pgcrypto"}:
                raise BackupError("restore_database_extension_missing")
            required_tables = ("identity.owner", "ops.audit_entry", "ops.job")
            for table in required_tables:
                if connection.scalar(text("SELECT to_regclass(:table)"), {"table": table}) is None:
                    raise BackupError("restore_database_table_missing")
                if connection.scalar(text(f"SELECT count(*) FROM {table}")) < 1:  # noqa: S608
                    raise BackupError("restore_database_record_missing")
            constraint_count = connection.scalar(
                text(
                    "SELECT count(*) FROM pg_constraint c "
                    "JOIN pg_namespace n ON n.oid = c.connamespace "
                    "WHERE n.nspname = ANY(:schemas)"
                ),
                {"schemas": list(REQUIRED_SCHEMAS)},
            )
            if not isinstance(constraint_count, int) or constraint_count < 1:
                raise BackupError("restore_database_constraint_missing")
    except BackupError:
        raise
    except SQLAlchemyError as exc:
        raise BackupError("restore_database_validation_failed") from exc


def _assert_alembic_head(engine: Engine) -> None:
    try:
        config = Config("/app/alembic.ini")
        code_heads = set(ScriptDirectory.from_config(config).get_heads())
        with engine.connect() as connection:
            restored_heads = set(
                connection.scalars(text("SELECT version_num FROM ops.alembic_version"))
            )
    except (OSError, SQLAlchemyError) as exc:
        raise BackupError("restore_alembic_head_unavailable") from exc
    if not code_heads or restored_heads != code_heads:
        raise BackupError("restore_alembic_head_mismatch")


def assert_restore_sentinel(engine: Engine) -> None:
    """Verify exact restore semantics without returning or logging canary values."""

    try:
        with engine.connect() as connection:
            row = connection.execute(
                text(
                    "SELECT marker_version, original_decimal, corrected_decimal, source, "
                    "correction_source, occurred_at, timezone, utc_offset_minutes "
                    "FROM ops.restore_sentinel WHERE id = :id"
                ),
                {"id": RESTORE_SENTINEL_ID},
            ).one_or_none()
    except SQLAlchemyError as exc:
        raise BackupError("restore_sentinel_unavailable") from exc
    if row is None:
        raise BackupError("restore_sentinel_missing")
    if tuple(row) != expected_restore_sentinel():
        raise BackupError("restore_sentinel_mismatch")


def assert_restore_canary(path: Path) -> None:
    """Open the non-health artifact canary without echoing its contents."""

    try:
        value: Any = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise BackupError("restore_artifact_canary_invalid") from exc
    if value != {
        "format_version": 1,
        "kind": "healthcurve_restore_canary",
        "synthetic": True,
    }:
        raise BackupError("restore_artifact_canary_mismatch")


def _ai_tables(engine: Engine) -> list[tuple[str, str]]:
    try:
        with engine.connect() as connection:
            rows = connection.execute(
                text(
                    "SELECT table_schema, table_name FROM information_schema.tables "
                    "WHERE table_schema IN ('fact','plan') AND table_type = 'BASE TABLE' "
                    "ORDER BY table_schema, table_name"
                )
            ).all()
            tables = [(str(row[0]), str(row[1])) for row in rows]
            if not tables or {schema for schema, _ in tables} != {"fact", "plan"}:
                raise BackupError("restore_safety_table_missing")
            for schema, table in tables:
                qualified = f"{schema}.{table}"
                if (
                    connection.scalar(
                        text("SELECT has_table_privilege('healthcurve_ai', :table, 'SELECT')"),
                        {"table": qualified},
                    )
                    is not True
                ):
                    raise BackupError("restore_ai_read_privilege_missing")
                for privilege in ("INSERT", "UPDATE", "DELETE", "TRUNCATE"):
                    if (
                        connection.scalar(
                            text(
                                "SELECT has_table_privilege('healthcurve_ai', :table, :privilege)"
                            ),
                            {"table": qualified, "privilege": privilege},
                        )
                        is not False
                    ):
                        raise BackupError("restore_ai_write_privilege_present")
            if (
                connection.scalar(
                    text("SELECT has_schema_privilege('healthcurve_ai', 'identity', 'USAGE')")
                )
                is not False
            ):
                raise BackupError("restore_ai_identity_privilege_present")
    except BackupError:
        raise
    except SQLAlchemyError as exc:
        raise BackupError("restore_ai_privilege_validation_failed") from exc
    return tables


def _assert_ai_write_denied(ai_engine: Engine, tables: list[tuple[str, str]]) -> None:
    for target_schema in ("fact", "plan"):
        schema, table = next(item for item in tables if item[0] == target_schema)
        quoted = ai_engine.dialect.identifier_preparer.quote
        # Identifiers come from information_schema and are quoted by the active
        # PostgreSQL dialect; values never enter this statement.
        statement = text(
            f"DELETE FROM {quoted(schema)}.{quoted(table)} WHERE false"  # noqa: S608
        )
        try:
            with ai_engine.begin() as connection:
                connection.execute(statement)
        except DBAPIError as exc:
            if getattr(exc.orig, "sqlstate", None) != "42501":
                raise BackupError("restore_ai_write_denial_unexpected") from exc
        else:
            raise BackupError("restore_ai_write_not_denied")


ApiSmoke = Callable[[Engine, str, Path, Path], None]


def run_drill(settings: DrillSettings, *, api_smoke: ApiSmoke) -> dict[str, Any]:
    with validated_restore_payload(
        settings.envelope,
        settings.identity,
        settings.work_root,
    ) as payload:
        assert_restore_canary(payload.restore_canary)
        _restore_database(payload.database_dump, settings.pg_restore)
        engine = create_engine(settings.database_url)
        ai_engine = create_engine(settings.ai_database_url)
        try:
            _assert_database_structure(engine)
            _assert_alembic_head(engine)
            assert_restore_sentinel(engine)
            tables = _ai_tables(engine)
            _assert_ai_write_denied(ai_engine, tables)
            api_smoke(
                engine,
                settings.ai_database_url,
                payload.uploads,
                payload.reports,
            )
        finally:
            ai_engine.dispose()
            engine.dispose()
        return {
            "status": "verified",
            "component_count": payload.component_count,
            "required_schema_count": len(REQUIRED_SCHEMAS),
            "alembic_head_verified": True,
            "restore_sentinel_verified": True,
            "artifact_canary_verified": True,
            "ai_write_denied": True,
            "api_smoke_verified": True,
        }
