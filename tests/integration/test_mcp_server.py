"""The local MCP server against the synthetic analytics database (ADR-0036).

Reuses the synthetic, init-script-provisioned database from test_analytics_views and
connects an in-process MCP client, so the full MCP request path runs over the real
view-only analyst roles.
"""

from __future__ import annotations

import json
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent
from sqlalchemy import Engine

from healthcurve.analysis.tools import AnalysisAccess
from healthcurve.mcp_server import build_server
from tests.integration import test_analytics_views as views_database

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

# Re-export the module-scoped container, role engines, and autouse synthetic seed.
postgres = views_database.postgres
owner_engine = views_database.owner_engine
analyst_engine = views_database.analyst_engine
analyst_text_engine = views_database.analyst_text_engine
seeded = views_database.seeded

SLEEP_SQL = (
    "SELECT count(*) AS nights, round(avg(bedtime_minutes_after_noon), 1) AS bedtime, "
    "round(avg(wake_minutes_after_midnight), 1) AS wake FROM analytics.sleep_nights "
    "WHERE wake_date BETWEEN DATE '2026-03-01' AND DATE '2026-03-31'"
)
TEXT_SQL = "SELECT count(*) AS entries FROM analytics_text.diary_entries"


def _body(result: CallToolResult) -> dict[str, Any]:
    block = result.content[0]
    assert isinstance(block, TextContent)
    return json.loads(block.text)


async def test_mcp_run_query_answers_from_the_analyst_role(analyst_engine: Engine) -> None:
    async with Client(build_server(AnalysisAccess(engine=analyst_engine))) as client:
        names = [tool.name for tool in (await client.list_tools()).tools]
        catalog = await client.call_tool("describe_data", {})
        result = await client.call_tool(
            "run_query", {"sql": SLEEP_SQL, "purpose": "synthetic March sleep timing"}
        )

    assert names == ["describe_data", "run_query", "clock_time_stats", "event_window_stats"]
    assert catalog.is_error is False
    assert _body(catalog)["data"]["text_access"] is False
    assert result.is_error is False
    data = _body(result)["data"]
    assert data["columns"] == ["nights", "bedtime", "wake"]
    assert data["rows"] == [[2, "720", "420"]]


async def test_mcp_text_views_need_the_opt_in_text_role(
    analyst_engine: Engine, analyst_text_engine: Engine
) -> None:
    arguments = {"sql": TEXT_SQL, "purpose": "synthetic diary count"}
    async with Client(build_server(AnalysisAccess(engine=analyst_engine))) as client:
        refused = await client.call_tool("run_query", arguments)
    text_access = AnalysisAccess(engine=None, text_engine=analyst_text_engine, allow_text=True)
    async with Client(build_server(text_access)) as client:
        allowed = await client.call_tool("run_query", arguments)

    assert refused.is_error is True
    assert _body(refused)["ok"] is False
    assert allowed.is_error is False
    assert _body(allowed)["data"]["columns"] == ["entries"]


async def test_mcp_invalid_sql_is_a_repairable_error_result(analyst_engine: Engine) -> None:
    async with Client(build_server(AnalysisAccess(engine=analyst_engine))) as client:
        result = await client.call_tool(
            "run_query", {"sql": "SELECT email FROM identity.owner", "purpose": "synthetic"}
        )

    assert result.is_error is True
    assert _body(result)["error"]["code"].startswith("query_")
