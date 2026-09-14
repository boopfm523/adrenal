"""Executing validated analysis queries under the view-only analyst roles (ADR-0036).

Reuses the synthetic, init-script-provisioned database from test_analytics_views.
"""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, text

from healthcurve.analysis.catalog import ANALYTICS_SCHEMA, EXAMPLE_QUERIES, TEXT_SCHEMA, VIEWS
from healthcurve.analysis.query import QueryError, ValidatedQuery, execute_query
from healthcurve.analysis.tools import AnalysisAccess, execute_analysis_tool
from tests.integration import test_analytics_views as views_database

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

# Re-export the module-scoped container, role engines, and autouse synthetic seed.
postgres = views_database.postgres
owner_engine = views_database.owner_engine
analyst_engine = views_database.analyst_engine
analyst_text_engine = views_database.analyst_text_engine
seeded = views_database.seeded


def test_catalog_matches_the_migrated_views_exactly(owner_engine: Engine) -> None:
    with owner_engine.connect() as conn:
        rows = conn.execute(
            text(
                "SELECT c.table_schema || '.' || c.table_name, c.column_name "
                "FROM information_schema.columns c JOIN information_schema.views v "
                "ON v.table_schema = c.table_schema AND v.table_name = c.table_name "
                "WHERE c.table_schema IN (:analytics, :text) "
                "ORDER BY c.table_schema, c.table_name, c.ordinal_position"
            ),
            {"analytics": ANALYTICS_SCHEMA, "text": TEXT_SCHEMA},
        ).all()
    database: dict[str, list[str]] = {}
    for view, column in rows:
        database.setdefault(view, []).append(column)
    catalog = {view.qualified_name: [column.name for column in view.columns] for view in VIEWS}
    assert database == catalog


def _run(access: AnalysisAccess, sql: str, **extra: object) -> dict[str, object]:
    output = execute_analysis_tool(access, "run_query", {"sql": sql, "purpose": "test", **extra})
    assert output.ok, (output.error_code, output.error_message)
    assert output.data is not None
    return output.data


def test_run_query_computes_answers_in_the_database(analyst_engine: Engine) -> None:
    access = AnalysisAccess(engine=analyst_engine)
    sleep = _run(
        access,
        "SELECT count(*) AS nights, round(avg(bedtime_minutes_after_noon), 1) AS bedtime, "
        "round(avg(wake_minutes_after_midnight), 1) AS wake FROM analytics.sleep_nights "
        "WHERE wake_date BETWEEN DATE '2026-03-01' AND DATE '2026-03-31'",
    )
    assert sleep["columns"] == ["nights", "bedtime", "wake"]
    assert sleep["rows"] == [[2, "720", "420"]]

    # A literal percent sign must survive extended-protocol execution.
    doses = _run(
        access,
        "SELECT round(avg(minutes_after_wake), 1) FROM analytics.doses "
        "WHERE medication_name LIKE '%hydrocortisone%'",
    )
    assert doses["rows"] == [["-25"]]


@pytest.mark.parametrize("question_sql", EXAMPLE_QUERIES, ids=lambda item: item[0][:40])
def test_catalog_example_queries_execute(
    analyst_engine: Engine,
    question_sql: tuple[str, str],
) -> None:
    _run(AnalysisAccess(engine=analyst_engine), question_sql[1])


def test_row_limit_marks_truncation(analyst_engine: Engine) -> None:
    data = _run(
        AnalysisAccess(engine=analyst_engine),
        "SELECT sleep_id FROM analytics.sleep_sessions ORDER BY start_at",
        row_limit=1,
    )
    assert data["row_count"] == 1
    assert data["truncated"] is True


@pytest.mark.safety("SAFE-15")
def test_database_enforces_the_boundary_even_if_validation_is_bypassed(
    analyst_engine: Engine,
) -> None:
    def bypassed(sql: str, **options: int) -> QueryError:
        with pytest.raises(QueryError) as caught:
            execute_query(analyst_engine, ValidatedQuery(sql=sql, views=()), **options)
        return caught.value

    assert bypassed("SELECT * FROM fact.dose_event").code == "query_permission_denied"
    assert bypassed("SELECT email FROM identity.owner").code == "query_permission_denied"
    assert (
        bypassed(
            "SELECT count(*) FROM analytics.doses; SELECT count(*) FROM analytics.symptoms"
        ).code
        == "query_invalid"
    )
    assert bypassed("SELECT pg_sleep(2) FROM analytics.doses", timeout_ms=100).code == (
        "query_timeout"
    )
    assert bypassed("DELETE FROM analytics.symptoms").code in {
        "query_invalid",
        "query_permission_denied",
    }


def test_database_errors_are_repairable_messages(analyst_engine: Engine) -> None:
    output = execute_analysis_tool(
        AnalysisAccess(engine=analyst_engine),
        "run_query",
        {"sql": "SELECT not_a_column FROM analytics.doses", "purpose": "test"},
    )
    assert not output.ok
    assert output.error_code == "query_invalid"
    assert output.error_message is not None and "does not exist" in output.error_message


def test_text_views_require_the_text_role(
    analyst_engine: Engine,
    analyst_text_engine: Engine,
) -> None:
    denied = execute_analysis_tool(
        AnalysisAccess(engine=analyst_engine, text_engine=analyst_text_engine),
        "run_query",
        {"sql": "SELECT text FROM analytics_text.record_notes", "purpose": "notes"},
    )
    assert not denied.ok and denied.error_code == "query_text_not_enabled"

    allowed = _run(
        AnalysisAccess(engine=analyst_engine, text_engine=analyst_text_engine, allow_text=True),
        "SELECT count(*) FROM analytics_text.record_notes",
    )
    assert allowed["rows"] == [[2]]
