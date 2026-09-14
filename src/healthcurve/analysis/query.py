"""Validated, read-only execution of model-authored SQL over the analytics views.

Validation is a usability and defense-in-depth layer: it gives the model precise,
repairable feedback and keeps queries inside the curated catalog. The security boundary
is the database role (``healthcurve_analyst``), which can read only the views. Queries
execute through the extended protocol, so PostgreSQL itself refuses multiple statements
even if this parser and the server ever disagreed about the text.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from dataclasses import dataclass
from decimal import Decimal
from typing import Any, Final

import sqlglot
from sqlalchemy import Engine
from sqlalchemy.exc import DBAPIError
from sqlglot import exp
from sqlglot.errors import ParseError, TokenError

from healthcurve.analysis.catalog import ANALYTICS_SCHEMA, TEXT_SCHEMA, VIEWS_BY_NAME

MAX_SQL_CHARS: Final = 8_000
DEFAULT_ROW_LIMIT: Final = 200
MAX_ROW_LIMIT: Final = 500
MAX_RESULT_CHARS: Final = 60_000
MAX_CELL_CHARS: Final = 500
STATEMENT_TIMEOUT_MS: Final = 10_000

#: sqlglot canonical names (lower-cased) of functions that are safe and useful for
#: analysis. Anything else, including clock functions that would make stored queries
#: unreproducible, is rejected with a message the model can act on.
ALLOWED_FUNCTIONS: Final = frozenset(
    {
        # aggregates and statistics
        "avg", "count", "sum", "min", "max", "stddev", "stddev_samp", "stddev_pop",
        "variance", "variance_pop", "percentile_cont", "percentile_disc", "mode", "corr",
        "covar_pop", "covar_samp", "regr_slope", "regr_intercept", "regr_r2",
        "logical_and", "logical_or", "array_agg", "group_concat",
        # window functions
        "row_number", "rank", "dense_rank", "percent_rank", "cume_dist", "ntile", "lag",
        "lead", "first_value", "last_value", "nth_value",
        # arithmetic and conditionals
        "round", "trunc", "floor", "ceil", "abs", "sqrt", "power", "ln", "log", "exp",
        "sign", "greatest", "least", "coalesce", "nullif", "cast", "try_cast", "case",
        "if", "width_bucket",
        # dates, times, and intervals
        "extract", "timestamp_trunc", "date_trunc", "time_to_str", "unix_to_time",
        "str_to_date", "str_to_time", "make_interval", "make_date", "time_from_parts",
        "timestamp_from_parts", "age", "justify_interval", "date", "date_bin",
        "exploding_generate_series",
        # text
        "lower", "upper", "length", "concat", "substring", "trim", "replace",
        "str_position", "split_part", "left", "right", "initcap", "regexp_replace",
        "regexp_like",
        # arrays
        "array_size", "explode",
    }
)  # fmt: skip

_FORBIDDEN_NODE_NAMES: Final = (
    "Alter", "Analyze", "Command", "Commit", "Copy", "Create", "Delete", "Describe",
    "Drop", "Grant", "Insert", "Kill", "Merge", "Pragma", "Revoke", "Rollback", "Set",
    "Show", "Transaction", "Update", "Use",
)  # fmt: skip
_FORBIDDEN_NODES: Final[tuple[type[exp.Expr], ...]] = tuple(
    node for name in _FORBIDDEN_NODE_NAMES if (node := getattr(exp, name, None)) is not None
)
_OPERATOR_NODES: Final[tuple[type[exp.Expr], ...]] = tuple(
    node
    for name in ("Binary", "Unary", "Connector", "Not", "Paren")
    if (node := getattr(exp, name, None)) is not None
)


class QueryError(Exception):
    """A refused or failed query, with a message safe and useful to return to the model."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(code)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class ValidatedQuery:
    sql: str
    views: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class QueryResult:
    columns: tuple[str, ...]
    rows: tuple[tuple[Any, ...], ...]
    truncated: bool
    views: tuple[str, ...]
    result_sha256: str

    def as_data(self) -> dict[str, Any]:
        return {
            "columns": list(self.columns),
            "rows": [list(row) for row in self.rows],
            "row_count": len(self.rows),
            "truncated": self.truncated,
            "views": list(self.views),
        }


def validate_query(sql: str, *, allow_text: bool) -> ValidatedQuery:
    """Accept exactly one read-only SELECT over the curated catalog, or raise QueryError."""

    stripped = sql.strip()
    if not stripped:
        raise QueryError("query_empty", "Provide one SELECT statement.")
    if len(stripped) > MAX_SQL_CHARS:
        raise QueryError(
            "query_too_long", f"The query exceeds {MAX_SQL_CHARS} characters; simplify it."
        )
    try:
        statements = [
            statement for statement in sqlglot.parse(stripped, read="postgres") if statement
        ]
    except (ParseError, TokenError) as exc:
        detail = str(exc).splitlines()[0][:300]
        raise QueryError("query_parse_error", f"Could not parse the SQL: {detail}") from None
    if len(statements) != 1:
        raise QueryError(
            "query_not_single_statement",
            "Submit exactly one SELECT statement, with no semicolon-separated commands.",
        )
    root = statements[0]
    if not isinstance(root, exp.Select | exp.SetOperation | exp.Subquery):
        raise QueryError(
            "query_not_select",
            "Only read-only SELECT queries (optionally WITH ... SELECT) are allowed.",
        )

    for node in root.walk():
        if isinstance(node, _FORBIDDEN_NODES):
            raise QueryError(
                "query_statement_forbidden",
                f"{type(node).__name__.upper()} is not allowed; queries are read-only SELECTs.",
            )
        if isinstance(node, exp.Placeholder | exp.Parameter):
            raise QueryError(
                "query_parameter_forbidden", "Use literal values instead of bind parameters."
            )
    for select in root.find_all(exp.Select):
        if select.args.get("into"):
            raise QueryError("query_into_forbidden", "SELECT INTO is not allowed.")
        if select.args.get("locks"):
            raise QueryError("query_lock_forbidden", "Row-locking clauses are not allowed.")

    views = _referenced_views(root, allow_text=allow_text)
    for function in root.find_all(exp.Func):
        if isinstance(function, _OPERATOR_NODES):
            continue  # sqlglot models some operators (AND, OR, NOT) as function nodes
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if name.lower() not in ALLOWED_FUNCTIONS:
            raise QueryError(
                "query_function_forbidden",
                f"Function {name.lower()}() is not available. Clock functions such as now() "
                "and current_date are unavailable: use literal dates such as "
                "DATE '2026-09-01'. Use standard aggregates, window, date, and text "
                "functions.",
            )
    if not views:
        raise QueryError(
            "query_no_view", "Query at least one analytics view, for example analytics.doses."
        )
    return ValidatedQuery(sql=stripped.rstrip(";").rstrip(), views=views)


def _referenced_views(root: exp.Expr, *, allow_text: bool) -> tuple[str, ...]:
    cte_names = {cte.alias_or_name.lower() for cte in root.find_all(exp.CTE)}
    allowed_schemas = {ANALYTICS_SCHEMA, TEXT_SCHEMA} if allow_text else {ANALYTICS_SCHEMA}
    views: set[str] = set()
    for table in root.find_all(exp.Table):
        if isinstance(table.this, exp.Func):
            continue  # a table function such as generate_series; checked with functions
        name = table.name.lower()
        schema = table.db.lower()
        if not schema and not table.catalog and name in cte_names:
            continue
        qualified = f"{schema}.{name}" if schema else name
        if schema == TEXT_SCHEMA and not allow_text and not table.catalog:
            raise QueryError(
                "query_text_not_enabled",
                "Free-text views in analytics_text are available only when the owner "
                "enables text for this conversation.",
            )
        if table.catalog or schema not in allowed_schemas or qualified not in VIEWS_BY_NAME:
            hint = (
                f" Did you mean {ANALYTICS_SCHEMA}.{name}?"
                if not schema and f"{ANALYTICS_SCHEMA}.{name}" in VIEWS_BY_NAME
                else ""
            )
            raise QueryError(
                "query_relation_forbidden",
                f"{qualified} is not an available view. Query only schema-qualified views "
                f"listed by describe_data.{hint}",
            )
        views.add(qualified)
    return tuple(sorted(views))


def execute_query(
    engine: Engine,
    query: ValidatedQuery,
    *,
    row_limit: int = DEFAULT_ROW_LIMIT,
    timeout_ms: int = STATEMENT_TIMEOUT_MS,
) -> QueryResult:
    """Run a validated query on a view-only analyst engine and shape a bounded result."""

    limit = max(1, min(int(row_limit), MAX_ROW_LIMIT))
    try:
        with engine.connect() as connection, connection.begin():
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")
            connection.exec_driver_sql(f"SET LOCAL statement_timeout = {int(timeout_ms)}")
            result = connection.execution_options(
                stream_results=True, max_row_buffer=limit + 1
            ).exec_driver_sql(
                # Parameters (even none) force the extended protocol, which rejects
                # multiple statements; literal percent signs must therefore be doubled.
                query.sql.replace("%", "%%"),
                (),
            )
            columns = tuple(str(key) for key in result.keys())
            fetched = result.fetchmany(limit + 1)
            result.close()
    except DBAPIError as exc:
        raise database_error(exc) from None

    truncated = len(fetched) > limit
    rows: list[tuple[Any, ...]] = []
    budget = MAX_RESULT_CHARS
    for raw in fetched[:limit]:
        row = tuple(jsonable(value) for value in raw)
        size = len(json.dumps(row, separators=(",", ":"), ensure_ascii=False))
        if size > budget:
            truncated = True
            break
        budget -= size
        rows.append(row)
    body = {"columns": columns, "rows": rows, "truncated": truncated}
    digest = hashlib.sha256(
        json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode()
    ).hexdigest()
    return QueryResult(
        columns=columns,
        rows=tuple(rows),
        truncated=truncated,
        views=query.views,
        result_sha256=digest,
    )


def database_error(exc: DBAPIError) -> QueryError:
    original = exc.orig
    diag = getattr(original, "diag", None)
    sqlstate = getattr(original, "sqlstate", None) or getattr(diag, "sqlstate", None)
    primary = getattr(diag, "message_primary", None) or "the database rejected the query"
    hint = getattr(diag, "message_hint", None)
    if sqlstate == "57014":
        return QueryError(
            "query_timeout",
            "The query exceeded the time limit. Narrow the date range, filter "
            "wearable_samples by metric_type, or aggregate before joining.",
        )
    if sqlstate == "42501":
        return QueryError(
            "query_permission_denied", "That relation is not available to analysis queries."
        )
    if sqlstate is None:
        return QueryError("query_unavailable", "The analysis database is unavailable.")
    message = str(primary)[:300]
    if hint:
        message = f"{message} Hint: {str(hint)[:200]}"
    return QueryError("query_invalid", f"PostgreSQL error {sqlstate}: {message}")


def jsonable(value: Any) -> Any:
    if value is None or isinstance(value, bool | int):
        return value
    if isinstance(value, str):
        return value if len(value) <= MAX_CELL_CHARS else value[:MAX_CELL_CHARS] + "…"
    if isinstance(value, Decimal):
        if not value.is_finite():
            return str(value)
        text = format(value, "f")
        return text.rstrip("0").rstrip(".") if "." in text else text
    if isinstance(value, float):
        return round(value, 6)
    if isinstance(value, dt.datetime | dt.date | dt.time):
        return value.isoformat()
    if isinstance(value, dt.timedelta):
        return f"{value.total_seconds():g} seconds"
    if isinstance(value, uuid.UUID):
        return str(value)
    if isinstance(value, list | tuple):
        return [jsonable(item) for item in value][:MAX_ROW_LIMIT]
    if isinstance(value, dict):
        return {str(key): jsonable(item) for key, item in value.items()}
    return str(value)[:MAX_CELL_CHARS]
