"""The analysis tool catalog shared by in-app chat and the local MCP server (ADR-0036).

Each tool has a strict argument model, a version, and a uniform output. Errors are
returned as data so a model can repair its call rather than failing the whole run.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Final

from pydantic import BaseModel, ConfigDict, Field, ValidationError
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from healthcurve.analysis.catalog import (
    CATALOG_VERSION,
    EXAMPLE_QUERIES,
    VIEWS,
    VIEWS_BY_NAME,
    View,
)
from healthcurve.analysis.helpers import (
    ClockTimeStatsArguments,
    EventWindowStatsArguments,
    ModeledExposureArguments,
    clock_time_stats,
    configured_modeled_exposure,
    event_window_stats,
    modeled_exposure,
)
from healthcurve.analysis.query import (
    DEFAULT_ROW_LIMIT,
    MAX_ROW_LIMIT,
    MAX_SQL_CHARS,
    QueryError,
    execute_query,
    validate_query,
)

CONVENTIONS: Final = (
    "Always schema-qualify views, for example analytics.sleep_nights.",
    "Use literal dates such as DATE '2026-09-01'; now() and current_date are unavailable. "
    "Today's local date is stated in the conversation.",
    "Columns ending in _at are UTC instants. local_* columns and local_date are the "
    "owner's experienced local time; filter local days with local_date.",
    "Missing data is an absent row or NULL, never zero. Report how many rows or days "
    "each result is based on.",
    "Do every calculation in SQL (count, avg, percentile_cont, extract(epoch FROM ...)/60) "
    "and round results. Do not compute numbers yourself.",
    "Recorded facts, physician-approved plans (medications, plan_* views), and provider "
    "summaries are different categories. A plan is not evidence that a dose was taken.",
)


@dataclass(frozen=True, slots=True)
class AnalysisAccess:
    """Database access for one analysis run. There is no fallback to a broader role."""

    engine: Engine | None
    text_engine: Engine | None = None
    allow_text: bool = False
    #: Modeled exposure needs domain services over base tables, so it runs in a
    #: caller-provided read-only session; omit it where that access is not wanted.
    owner_id: uuid.UUID | None = None
    timezone: str | None = None
    model_session_factory: Callable[[], Session] | None = None

    def engine_for_query(self) -> Engine:
        engine = self.text_engine if self.allow_text else self.engine
        if engine is None:
            raise QueryError(
                "analysis_not_configured",
                "Analysis queries are not configured on this HealthCurve installation.",
            )
        return engine


@dataclass(frozen=True, slots=True)
class ToolOutput:
    tool_name: str
    tool_version: str
    ok: bool
    data: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
    views: tuple[str, ...] = ()
    result_sha256: str = field(default="")

    def as_model_content(self) -> str:
        """Compact JSON for a model tool-result message."""

        body: dict[str, Any] = {"ok": self.ok}
        if self.ok:
            body["data"] = self.data
        else:
            body["error"] = {"code": self.error_code, "message": self.error_message}
        return json.dumps(body, separators=(",", ":"), ensure_ascii=False, default=str)


class DescribeDataArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    views: list[str] | None = Field(
        default=None,
        max_length=12,
        description=(
            "Schema-qualified view names to describe in full (columns, units, meanings). "
            "Omit for the compact catalog with conventions and example queries."
        ),
    )


class RunQueryArguments(BaseModel):
    model_config = ConfigDict(extra="forbid")

    sql: str = Field(
        min_length=1,
        max_length=MAX_SQL_CHARS,
        description=(
            "Exactly one read-only PostgreSQL SELECT (WITH allowed) over schema-qualified "
            "analytics views. Use literal dates and do all arithmetic in SQL."
        ),
    )
    purpose: str = Field(
        min_length=1,
        max_length=300,
        description="One short sentence stating what the query computes, shown to the owner.",
    )
    row_limit: int = Field(
        default=DEFAULT_ROW_LIMIT,
        ge=1,
        le=MAX_ROW_LIMIT,
        description="Maximum rows to return; prefer aggregated queries over many rows.",
    )


@dataclass(frozen=True, slots=True)
class AnalysisTool:
    name: str
    version: str
    description: str
    arguments: type[BaseModel]
    handler: Callable[[AnalysisAccess, Any], ToolOutput]

    def definition(self) -> dict[str, Any]:
        """Ollama/OpenAI-style function tool definition."""

        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.arguments.model_json_schema(),
            },
        }


def _view_summary(view: View) -> dict[str, Any]:
    return {
        "view": view.qualified_name,
        "category": view.category.value,
        "grain": view.grain,
        "columns": [column.name for column in view.columns],
    }


def _view_detail(view: View) -> dict[str, Any]:
    return {
        "view": view.qualified_name,
        "category": view.category.value,
        "grain": view.grain,
        "description": view.description,
        "columns": [
            {
                "name": column.name,
                "type": column.kind,
                **({"meaning": column.meaning} if column.meaning else {}),
            }
            for column in view.columns
        ],
    }


def _describe_data(access: AnalysisAccess, arguments: DescribeDataArguments) -> ToolOutput:
    visible = [view for view in VIEWS if access.allow_text or not view.requires_text_access]
    if arguments.views:
        unknown = [name for name in arguments.views if name not in VIEWS_BY_NAME]
        hidden = [
            name
            for name in arguments.views
            if name in VIEWS_BY_NAME and VIEWS_BY_NAME[name] not in visible
        ]
        if unknown or hidden:
            return _failure(
                DESCRIBE_DATA,
                "view_unknown" if unknown else "query_text_not_enabled",
                f"Unknown views: {', '.join(unknown)}."
                if unknown
                else "Free-text views are available only when the owner enables text.",
            )
        data: dict[str, Any] = {
            "catalog_version": CATALOG_VERSION,
            "views": [_view_detail(VIEWS_BY_NAME[name]) for name in arguments.views],
        }
    else:
        data = {
            "catalog_version": CATALOG_VERSION,
            "text_access": access.allow_text,
            "conventions": list(CONVENTIONS),
            "views": [_view_summary(view) for view in visible],
            "example_queries": [
                {"question": question, "sql": sql} for question, sql in EXAMPLE_QUERIES
            ],
        }
    return _success(DESCRIBE_DATA, data)


def _run_query(access: AnalysisAccess, arguments: RunQueryArguments) -> ToolOutput:
    try:
        validated = validate_query(arguments.sql, allow_text=access.allow_text)
        result = execute_query(access.engine_for_query(), validated, row_limit=arguments.row_limit)
    except QueryError as exc:
        return _failure(RUN_QUERY, exc.code, exc.message)
    return ToolOutput(
        tool_name=RUN_QUERY.name,
        tool_version=RUN_QUERY.version,
        ok=True,
        data=result.as_data(),
        views=result.views,
        result_sha256=result.result_sha256,
    )


def _success(tool: AnalysisTool, data: dict[str, Any]) -> ToolOutput:
    return ToolOutput(
        tool_name=tool.name,
        tool_version=tool.version,
        ok=True,
        data=data,
        result_sha256=_digest(data),
    )


def _failure(tool: AnalysisTool, code: str, message: str) -> ToolOutput:
    return ToolOutput(
        tool_name=tool.name,
        tool_version=tool.version,
        ok=False,
        error_code=code,
        error_message=message,
        result_sha256=_digest({"error": code}),
    )


def _digest(value: Any) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, default=str)
    return hashlib.sha256(body.encode()).hexdigest()


DESCRIBE_DATA: Final = AnalysisTool(
    name="describe_data",
    version="hc-analysis-describe-v1",
    description=(
        "List the HealthCurve analytics views with their grain, columns, query conventions, "
        "and example queries. Pass views to get column meanings and units."
    ),
    arguments=DescribeDataArguments,
    handler=_describe_data,  # type: ignore[arg-type]
)

RUN_QUERY: Final = AnalysisTool(
    name="run_query",
    version="hc-analysis-query-v1",
    description=(
        "Run one read-only PostgreSQL SELECT over the analytics views and return columns "
        "and rows. Errors explain how to fix the query."
    ),
    arguments=RunQueryArguments,
    handler=_run_query,  # type: ignore[arg-type]
)


def _clock_time_stats(access: AnalysisAccess, arguments: ClockTimeStatsArguments) -> ToolOutput:
    try:
        data = clock_time_stats(access.engine_for_query(), arguments)
    except QueryError as exc:
        return _failure(CLOCK_TIME_STATS, exc.code, exc.message)
    return _success(CLOCK_TIME_STATS, data)


def _event_window_stats(access: AnalysisAccess, arguments: EventWindowStatsArguments) -> ToolOutput:
    try:
        data = event_window_stats(access.engine_for_query(), arguments)
    except QueryError as exc:
        return _failure(EVENT_WINDOW_STATS, exc.code, exc.message)
    return _success(EVENT_WINDOW_STATS, data)


def _modeled_exposure(access: AnalysisAccess, arguments: ModeledExposureArguments) -> ToolOutput:
    try:
        factory, owner_id, timezone = configured_modeled_exposure(
            access.model_session_factory, access.owner_id, access.timezone
        )
        data = modeled_exposure(factory, owner_id=owner_id, timezone=timezone, arguments=arguments)
    except QueryError as exc:
        return _failure(MODELED_EXPOSURE, exc.code, exc.message)
    return _success(MODELED_EXPOSURE, data)


CLOCK_TIME_STATS: Final = AnalysisTool(
    name="clock_time_stats",
    version="hc-analysis-clock-v1",
    description=(
        "Average (circular mean), median, earliest, and latest local clock time of bedtimes, "
        "wake times, doses (all or first per day), meals, symptoms, or activity starts over "
        "a date range. Use this instead of averaging clock times in SQL."
    ),
    arguments=ClockTimeStatsArguments,
    handler=_clock_time_stats,  # type: ignore[arg-type]
)

EVENT_WINDOW_STATS: Final = AnalysisTool(
    name="event_window_stats",
    version="hc-analysis-window-v1",
    description=(
        "For each symptom, dose, activity, meal, stress episode, wake, or bedtime in a date "
        "range, summarize heart rate, stress, respiration, or HRV samples in the minutes "
        "before and after it, with per-event rows and an overall summary."
    ),
    arguments=EventWindowStatsArguments,
    handler=_event_window_stats,  # type: ignore[arg-type]
)

MODELED_EXPOSURE: Final = AnalysisTool(
    name="modeled_exposure",
    version="hc-analysis-exposure-v1",
    description=(
        "Modeled theoretical free-cortisol values for a local date from recorded doses, "
        "with the recorded-reference band position. Modeled analysis, not a measurement."
    ),
    arguments=ModeledExposureArguments,
    handler=_modeled_exposure,  # type: ignore[arg-type]
)

TOOLS: Final[dict[str, AnalysisTool]] = {
    tool.name: tool
    for tool in (DESCRIBE_DATA, RUN_QUERY, CLOCK_TIME_STATS, EVENT_WINDOW_STATS, MODELED_EXPOSURE)
}


def tool_definitions(access: AnalysisAccess | None = None) -> list[dict[str, Any]]:
    """Tool definitions; with ``access``, omit tools that context cannot run."""

    hidden = (
        {MODELED_EXPOSURE.name}
        if access is not None and access.model_session_factory is None
        else set()
    )
    return [tool.definition() for name, tool in TOOLS.items() if name not in hidden]


def execute_analysis_tool(
    access: AnalysisAccess, tool_name: str, arguments: dict[str, Any]
) -> ToolOutput:
    """Validate arguments and run one catalog tool; never raises for model mistakes."""

    tool = TOOLS.get(tool_name)
    if tool is None:
        return ToolOutput(
            tool_name=tool_name,
            tool_version="unknown",
            ok=False,
            error_code="tool_unknown",
            error_message=f"Unknown tool. Available tools: {', '.join(TOOLS)}.",
        )
    try:
        parsed = tool.arguments.model_validate(arguments)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in error['loc']) or 'arguments'}: {error['msg']}"
            for error in exc.errors()[:5]
        )
        return _failure(tool, "arguments_invalid", f"Invalid arguments: {problems}")
    return tool.handler(access, parsed)
