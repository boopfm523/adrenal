"""The analytical evaluation fixture: independent truth equals what the tools compute.

Seeds the synthetic dataset into PostgreSQL provisioned by the real init scripts and
checks that the analytics views and analysis tools reach the same answers as the plain
Python ground truth for every owner example question (ADR-0036). Synthetic data only.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.analysis.tools import AnalysisAccess, execute_analysis_tool
from healthcurve.analytical_evaluation import (
    SYNTHETIC_MARKER,
    TIMEZONE,
    TODAY,
    compute_truth,
    generate_dataset,
    seed_database,
)
from healthcurve.config import get_settings

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORDS = {  # ephemeral container credentials
    "healthcurve": "owner-test-password",  # pragma: allowlist secret
    "healthcurve_ai": "ai-test-password",  # pragma: allowlist secret
    "healthcurve_analyst": "analyst-test-password",  # pragma: allowlist secret
    "healthcurve_analyst_text": "analyst-text-test-password",  # pragma: allowlist secret
}
START_30 = (TODAY - timedelta(days=29)).isoformat()
START_14 = (TODAY - timedelta(days=13)).isoformat()
END = TODAY.isoformat()


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    container = (
        PostgresContainer(
            "postgres:16-alpine",
            username="healthcurve",
            password=PASSWORDS["healthcurve"],
            dbname="healthcurve",
            driver="psycopg",
        )
        .with_env("POSTGRES_AI_PASSWORD", PASSWORDS["healthcurve_ai"])
        .with_env("POSTGRES_ANALYST_PASSWORD", PASSWORDS["healthcurve_analyst"])
        .with_env("POSTGRES_ANALYST_TEXT_PASSWORD", PASSWORDS["healthcurve_analyst_text"])
        .with_volume_mapping(
            str(REPO_ROOT / "deploy" / "postgres-init"), "/docker-entrypoint-initdb.d", "ro"
        )
    )
    with container as running:
        config = Config(str(REPO_ROOT / "alembic.ini"))
        config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
        with mock.patch.dict(os.environ, {"HC_DATABASE_URL": running.get_connection_url()}):
            get_settings.cache_clear()
            command.upgrade(config, "head")
        get_settings.cache_clear()
        yield running


def _engine(postgres: PostgresContainer, role: str) -> Engine:
    return create_engine(
        postgres.get_connection_url().replace(
            f"healthcurve:{PASSWORDS['healthcurve']}@", f"{role}:{PASSWORDS[role]}@"
        )
    )


@pytest.fixture(scope="module")
def access(postgres: PostgresContainer) -> Iterator[AnalysisAccess]:
    owner_engine = _engine(postgres, "healthcurve")
    with Session(owner_engine) as session, session.begin():
        owner_id: uuid.UUID = seed_database(session, generate_dataset())
    engines = [owner_engine] + [
        _engine(postgres, role)
        for role in ("healthcurve_analyst", "healthcurve_analyst_text", "healthcurve_ai")
    ]
    yield AnalysisAccess(
        engine=engines[1],
        text_engine=engines[2],
        owner_id=owner_id,
        timezone=TIMEZONE,
        model_session_factory=sessionmaker(engines[3]),
    )
    for engine in engines:
        engine.dispose()


@pytest.fixture(scope="module")
def truth() -> dict[str, Any]:
    return compute_truth(generate_dataset())


def _data(access: AnalysisAccess, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    output = execute_analysis_tool(access, tool, arguments)
    assert output.ok, (output.error_code, output.error_message)
    assert output.data is not None
    return output.data


def _query(access: AnalysisAccess, sql: str) -> list[Any]:
    data = _data(access, "run_query", {"sql": sql, "purpose": "fixture check"})
    return data["rows"]


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def test_clock_helpers_match_independent_bedtime_and_wake_truth(
    access: AnalysisAccess, truth: dict[str, Any]
) -> None:
    for source, key in (("bedtime", "avg_bedtime_30d"), ("wake", "avg_wake_30d")):
        data = _data(
            access, "clock_time_stats", {"source": source, "date_from": START_30, "date_to": END}
        )
        assert data["summary"]["count"] == truth["nights_30d"]
        # Circular and arithmetic means agree within a minute for tightly clustered times.
        difference = abs(_minutes(data["summary"]["mean_clock"]) - _minutes(truth[key]))
        assert min(difference, 1_440 - difference) <= 1, (source, data["summary"], truth[key])


def test_queries_match_steps_first_dose_activity_and_missing_day_truth(
    access: AnalysisAccess, truth: dict[str, Any]
) -> None:
    window = f"BETWEEN DATE '{START_30}' AND DATE '{END}'"
    assert _query(
        access,
        "SELECT count(steps), round(avg(steps), 0) FROM analytics.daily_wearables "
        f"WHERE local_date {window}",
    ) == [[truth["step_days_30d"], truth["avg_steps_30d"]]]
    assert _query(
        access,
        "WITH first_dose AS (SELECT DISTINCT ON (local_date) local_date, minutes_after_wake "
        f"FROM analytics.doses WHERE local_date {window} ORDER BY local_date, taken_at) "
        "SELECT count(minutes_after_wake), round(avg(minutes_after_wake), 1) FROM first_dose",
    ) == [[truth["first_dose_days_30d"], truth["avg_minutes_wake_to_first_dose_30d"]]]
    assert _query(
        access,
        f"SELECT count(DISTINCT local_date) FROM analytics.activities WHERE local_date {window}",
    ) == [[truth["activity_days_30d"]]]
    assert _query(
        access,
        f"SELECT 30 - count(steps) FROM analytics.daily_wearables WHERE local_date {window}",
    ) == [[truth["missing_step_days_30d"]]]


def test_event_windows_match_symptom_heart_rate_truth(
    access: AnalysisAccess, truth: dict[str, Any]
) -> None:
    data = _data(
        access,
        "event_window_stats",
        {
            "event_source": "symptom",
            "date_from": START_14,
            "date_to": END,
            "metrics": ["heart_rate"],
            "minutes_before": 60,
            "minutes_after": 60,
            "include_per_event": False,
        },
    )
    assert data["events_total"] == truth["symptom_events_14d"]
    summary = data["summary"]["heart_rate"]
    # Tool output drops trailing zeros ("71"); compare values, not formatting.
    assert Decimal(summary["mean_before"]) == Decimal(truth["avg_hr_before_symptoms_14d"])
    assert Decimal(summary["mean_after"]) == Decimal(truth["avg_hr_after_symptoms_14d"])


def test_injection_diary_is_only_readable_through_text_access(access: AnalysisAccess) -> None:
    denied = execute_analysis_tool(
        access,
        "run_query",
        {"sql": "SELECT text FROM analytics_text.diary_entries", "purpose": "diary"},
    )
    assert not denied.ok and denied.error_code == "query_text_not_enabled"
    text_access = AnalysisAccess(
        engine=access.engine, text_engine=access.text_engine, allow_text=True
    )
    rows = _query(text_access, "SELECT text FROM analytics_text.diary_entries")
    assert len(rows) == 1 and SYNTHETIC_MARKER in rows[0][0]
