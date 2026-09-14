"""Validation of model-authored analysis SQL (ADR-0036). No database required."""

from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal

import pytest

from healthcurve.analysis.catalog import EXAMPLE_QUERIES, TEXT_SCHEMA, VIEWS
from healthcurve.analysis.query import (
    MAX_SQL_CHARS,
    QueryError,
    _jsonable,  # pyright: ignore[reportPrivateUsage]
    validate_query,
)
from healthcurve.analysis.tools import (
    AnalysisAccess,
    execute_analysis_tool,
    tool_definitions,
)


@pytest.mark.parametrize("question_sql", EXAMPLE_QUERIES, ids=lambda item: item[0][:40])
def test_catalog_example_queries_validate(question_sql: tuple[str, str]) -> None:
    validate_query(question_sql[1], allow_text=False)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT count(*) FROM analytics.doses",
        "SELECT count(*) FROM analytics.doses;",
        "WITH n AS (SELECT * FROM analytics.sleep_nights) SELECT avg(in_bed_minutes) FROM n",
        "SELECT percentile_cont(0.5) WITHIN GROUP (ORDER BY steps) FROM analytics.daily_wearables",
        "SELECT local_date, row_number() OVER (PARTITION BY local_date ORDER BY taken_at), "
        "lag(taken_at) OVER (ORDER BY taken_at) FROM analytics.doses",
        "SELECT d::date, count(a.activity_id) FROM generate_series(DATE '2026-08-01', "
        "DATE '2026-08-31', interval '1 day') AS d LEFT JOIN analytics.activities a "
        "ON a.local_date = d::date GROUP BY 1 ORDER BY 1",
        "SELECT to_char(wake_clock, 'HH24:MI'), date_trunc('week', wake_date), "
        "extract(epoch FROM wake_at - bedtime_at) / 60 FROM analytics.sleep_nights",
        "SELECT name FROM analytics.symptoms WHERE name ILIKE '%fatigue%'",
        "SELECT corr(steps, resting_heart_rate_bpm), round(stddev_samp(steps), 1) "
        "FROM analytics.daily_wearables",
        "SELECT 1 FROM analytics.meals UNION ALL SELECT 2 FROM analytics.symptoms",
        "SELECT s.local_time, avg(w.value) FROM analytics.symptoms s JOIN "
        "analytics.wearable_samples w ON w.sample_at BETWEEN s.occurred_at - interval "
        "'1 hour' AND s.occurred_at + make_interval(mins => 60) GROUP BY 1",
        # Boolean operators are modeled as function nodes by sqlglot; they must pass.
        "SELECT count(*) FROM analytics.doses WHERE (category = 'stress' OR category = "
        "'emergency') AND NOT (minutes_after_wake IS NULL) AND amount > 0",
        "SELECT count(*) FILTER (WHERE severity >= 5 AND name <> 'x') "
        "FROM analytics.symptoms WHERE local_date >= DATE '2026-09-01'",
    ],
)
def test_analytical_select_shapes_are_accepted(sql: str) -> None:
    validated = validate_query(sql, allow_text=False)
    assert validated.views
    assert not validated.sql.endswith(";")


@pytest.mark.parametrize(
    ("sql", "code"),
    [
        ("", "query_empty"),
        ("SELEC count(*) FRM", "query_parse_error"),
        (
            "SELECT 1 FROM analytics.doses; DELETE FROM analytics.doses",
            "query_not_single_statement",
        ),
        (
            "SELECT 1 FROM analytics.doses; SELECT 2 FROM analytics.doses",
            "query_not_single_statement",
        ),
        ("DELETE FROM analytics.doses", "query_not_select"),
        ("INSERT INTO analytics.meals VALUES (1)", "query_not_select"),
        ("UPDATE analytics.doses SET amount = 0", "query_not_select"),
        ("DROP VIEW analytics.doses", "query_not_select"),
        ("CREATE TABLE analytics.x AS SELECT 1", "query_not_select"),
        ("COPY (SELECT 1) TO PROGRAM 'id'", "query_not_select"),
        ("SET ROLE healthcurve", "query_not_select"),
        (
            "WITH gone AS (DELETE FROM analytics.doses RETURNING *) SELECT * FROM gone",
            "query_statement_forbidden",
        ),
        ("SELECT * INTO analytics.copy FROM analytics.doses", "query_into_forbidden"),
        ("SELECT * FROM analytics.doses FOR UPDATE", "query_lock_forbidden"),
        ("SELECT * FROM fact.dose_event", "query_relation_forbidden"),
        ("SELECT * FROM pg_catalog.pg_roles", "query_relation_forbidden"),
        ("SELECT * FROM information_schema.tables", "query_relation_forbidden"),
        (
            "SELECT (SELECT email FROM identity.owner) FROM analytics.doses",
            "query_relation_forbidden",
        ),
        ("SELECT * FROM doses", "query_relation_forbidden"),
        ("SELECT * FROM healthcurve.analytics.doses", "query_relation_forbidden"),
        ("SELECT * FROM analytics.not_a_view", "query_relation_forbidden"),
        ("SELECT * FROM analytics_text.diary_entries", "query_text_not_enabled"),
        ("SELECT pg_sleep(30) FROM analytics.doses", "query_function_forbidden"),
        (
            "SELECT set_config('role', 'healthcurve', false) FROM analytics.doses",
            "query_function_forbidden",
        ),
        ("SELECT current_setting('is_superuser') FROM analytics.doses", "query_function_forbidden"),
        ("SELECT * FROM analytics.doses WHERE taken_at > now()", "query_function_forbidden"),
        (
            "SELECT * FROM analytics.doses WHERE local_date = current_date",
            "query_function_forbidden",
        ),
        ("SELECT random() FROM analytics.doses", "query_function_forbidden"),
        ("SELECT * FROM dblink('host=x', 'SELECT 1') AS t(a int)", "query_function_forbidden"),
        ("SELECT * FROM analytics.doses WHERE amount = $1", "query_parameter_forbidden"),
        ("SELECT 1", "query_no_view"),
    ],
)
def test_unsafe_or_out_of_catalog_queries_are_rejected(sql: str, code: str) -> None:
    with pytest.raises(QueryError) as caught:
        validate_query(sql, allow_text=False)
    assert caught.value.code == code
    assert caught.value.message


def test_overlong_sql_is_rejected() -> None:
    with pytest.raises(QueryError) as caught:
        validate_query(
            "SELECT 1 FROM analytics.doses " + " " * MAX_SQL_CHARS + "x", allow_text=False
        )
    assert caught.value.code == "query_too_long"


def test_unqualified_view_names_get_a_repair_hint() -> None:
    with pytest.raises(QueryError) as caught:
        validate_query("SELECT * FROM sleep_nights", allow_text=False)
    assert "analytics.sleep_nights" in caught.value.message


def test_text_views_require_explicit_text_access() -> None:
    sql = f"SELECT text FROM {TEXT_SCHEMA}.diary_entries"
    assert validate_query(sql, allow_text=True).views == (f"{TEXT_SCHEMA}.diary_entries",)


def test_result_values_are_json_safe_and_compact() -> None:
    assert _jsonable(Decimal("8000.0000")) == "8000"
    assert _jsonable(Decimal("12.3400")) == "12.34"
    assert _jsonable(dt.datetime(2026, 3, 8, 7, 15, tzinfo=dt.UTC)) == "2026-03-08T07:15:00+00:00"
    assert _jsonable(dt.time(23, 30)) == "23:30:00"
    assert _jsonable(dt.timedelta(minutes=5)) == "300 seconds"
    assert _jsonable(uuid.UUID(int=1)) == "00000000-0000-0000-0000-000000000001"
    assert _jsonable("x" * 1_000).endswith("…")


def test_tool_definitions_are_strict_function_schemas() -> None:
    definitions = {item["function"]["name"]: item for item in tool_definitions()}
    assert set(definitions) == {"describe_data", "run_query"}
    run_query = definitions["run_query"]["function"]["parameters"]
    assert run_query["additionalProperties"] is False
    assert set(run_query["required"]) == {"sql", "purpose"}


def test_describe_data_hides_text_views_without_text_access() -> None:
    compact = execute_analysis_tool(AnalysisAccess(engine=None), "describe_data", {})
    assert compact.ok and compact.data is not None
    names = {item["view"] for item in compact.data["views"]}
    assert names == {view.qualified_name for view in VIEWS if not view.requires_text_access}
    assert compact.data["example_queries"]

    detail = execute_analysis_tool(
        AnalysisAccess(engine=None), "describe_data", {"views": ["analytics.sleep_nights"]}
    )
    assert detail.ok and detail.data is not None
    columns = detail.data["views"][0]["columns"]
    assert any(column.get("meaning") for column in columns)

    hidden = execute_analysis_tool(
        AnalysisAccess(engine=None), "describe_data", {"views": ["analytics_text.diary_entries"]}
    )
    assert not hidden.ok and hidden.error_code == "query_text_not_enabled"


def test_tool_argument_and_configuration_errors_are_returned_as_data() -> None:
    access = AnalysisAccess(engine=None)
    unknown = execute_analysis_tool(access, "drop_everything", {})
    assert not unknown.ok and unknown.error_code == "tool_unknown"

    extra = execute_analysis_tool(
        access,
        "run_query",
        {"sql": "SELECT 1 FROM analytics.doses", "purpose": "x", "owner_id": "y"},
    )
    assert not extra.ok and extra.error_code == "arguments_invalid"

    rejected = execute_analysis_tool(
        access, "run_query", {"sql": "SELECT * FROM fact.dose_event", "purpose": "probe"}
    )
    assert not rejected.ok and rejected.error_code == "query_relation_forbidden"

    unconfigured = execute_analysis_tool(
        access, "run_query", {"sql": "SELECT count(*) FROM analytics.doses", "purpose": "count"}
    )
    assert not unconfigured.ok and unconfigured.error_code == "analysis_not_configured"
    assert '"ok":false' in unconfigured.as_model_content()
