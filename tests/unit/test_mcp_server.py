"""The local MCP server's tool surface, access selection, and HTTP guard (ADR-0036).

Synthetic only: engines are opaque placeholders and no database or network is used.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from typing import Any, cast

import pytest
from mcp import Client
from mcp.types import CallToolResult, TextContent
from pydantic import BaseModel, ValidationError
from sqlalchemy import Engine
from sqlalchemy.exc import ArgumentError
from starlette.testclient import TestClient
from structlog.testing import capture_logs

from healthcurve import mcp_server
from healthcurve.analysis.tools import AnalysisAccess, ToolOutput, tool_definitions

SESSION_FREE_TOOLS = ["describe_data", "run_query", "clock_time_stats", "event_window_stats"]
ENGINE = cast(Engine, object())
TEXT_ENGINE = cast(Engine, object())
PORT = 8766
SYNTHETIC_MARKER = "synthetic-credential-marker"

INITIALIZE = {
    "jsonrpc": "2.0",
    "id": 1,
    "method": "initialize",
    "params": {
        "protocolVersion": "2025-06-18",
        "capabilities": {},
        "clientInfo": {"name": "synthetic-local-client", "version": "1"},
    },
}
MCP_HEADERS = {"accept": "application/json, text/event-stream", "content-type": "application/json"}


@pytest.fixture(autouse=True)
def _isolated_process(monkeypatch: pytest.MonkeyPatch) -> None:
    # main() would otherwise bind global logging to this test's captured stderr.
    monkeypatch.setattr(mcp_server, "configure_process_logging", lambda: None)
    monkeypatch.delenv(mcp_server.ALLOW_TEXT_ENV, raising=False)


def _no_serving(monkeypatch: pytest.MonkeyPatch) -> None:
    def refuse(*_args: Any, **_kwargs: Any) -> None:
        raise AssertionError("the server must not start")

    monkeypatch.setattr(mcp_server, "serve_stdio", refuse)
    monkeypatch.setattr(mcp_server, "serve_http", refuse)


def _body(result: CallToolResult) -> dict[str, Any]:
    assert len(result.content) == 1
    block = result.content[0]
    assert isinstance(block, TextContent)
    return json.loads(block.text)


# ---------------------------------------------------------------------------
# Tool surface
# ---------------------------------------------------------------------------


async def test_lists_exactly_the_session_free_tools_with_catalog_schemas() -> None:
    access = AnalysisAccess(engine=ENGINE)
    async with Client(mcp_server.build_server(access)) as client:
        listed = (await client.list_tools()).tools

    assert [tool.name for tool in listed] == SESSION_FREE_TOOLS
    catalog = {item["function"]["name"]: item["function"] for item in tool_definitions(access)}
    for tool in listed:
        assert tool.input_schema == catalog[tool.name]["parameters"]
        assert tool.description == catalog[tool.name]["description"]


def test_refuses_an_owner_session_factory() -> None:
    access = AnalysisAccess(engine=ENGINE, model_session_factory=cast(Any, lambda: None))
    with pytest.raises(ValueError, match="owner session"):
        mcp_server.build_server(access)


async def test_a_call_is_dispatched_through_the_shared_catalog(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[AnalysisAccess, str, dict[str, Any]]] = []
    data = {"columns": ["n"], "rows": [[3]], "row_count": 1, "truncated": False}

    def fake(access: AnalysisAccess, name: str, arguments: dict[str, Any]) -> ToolOutput:
        calls.append((access, name, arguments))
        return ToolOutput(tool_name=name, tool_version="synthetic-v1", ok=True, data=data)

    monkeypatch.setattr(mcp_server, "execute_analysis_tool", fake)
    access = AnalysisAccess(engine=ENGINE)
    arguments = {"sql": "SELECT count(*) AS n FROM analytics.doses", "purpose": "count doses"}
    async with Client(mcp_server.build_server(access)) as client:
        result = await client.call_tool("run_query", arguments)

    assert len(calls) == 1
    assert calls[0][0] is access
    assert calls[0][1:] == ("run_query", arguments)
    assert result.is_error is False
    assert _body(result) == {"ok": True, "data": data}


def test_a_tool_error_is_returned_as_an_mcp_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(_access: AnalysisAccess, name: str, _arguments: dict[str, Any]) -> ToolOutput:
        return ToolOutput(
            tool_name=name,
            tool_version="synthetic-v1",
            ok=False,
            error_code="query_invalid",
            error_message="Use exactly one SELECT.",
        )

    monkeypatch.setattr(mcp_server, "execute_analysis_tool", fake)
    result = mcp_server.call_analysis_tool(
        AnalysisAccess(engine=ENGINE), frozenset(SESSION_FREE_TOOLS), "run_query", {"sql": "x"}
    )
    assert result.is_error is True
    assert _body(result) == {
        "ok": False,
        "error": {"code": "query_invalid", "message": "Use exactly one SELECT."},
    }


def test_modeled_exposure_is_not_callable(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(*_args: Any) -> ToolOutput:
        raise AssertionError("an unoffered tool must not reach the catalog")

    monkeypatch.setattr(mcp_server, "execute_analysis_tool", fake)
    result = mcp_server.call_analysis_tool(
        AnalysisAccess(engine=ENGINE),
        frozenset(SESSION_FREE_TOOLS),
        "modeled_exposure",
        {"local_date": "2026-03-01"},
    )
    assert result.is_error is True
    assert _body(result)["error"]["code"] == "tool_unknown"


@pytest.mark.safety("SAFE-29")
def test_logs_carry_only_operational_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    sql = "SELECT symptom_name FROM analytics.symptoms WHERE severity > 7"

    def fake(_access: AnalysisAccess, name: str, _arguments: dict[str, Any]) -> ToolOutput:
        return ToolOutput(
            tool_name=name,
            tool_version="synthetic-v1",
            ok=True,
            data={"columns": ["symptom_name"], "rows": [["synthetic headache"]], "row_count": 1},
        )

    monkeypatch.setattr(mcp_server, "execute_analysis_tool", fake)
    with capture_logs() as logs:
        mcp_server.call_analysis_tool(
            AnalysisAccess(engine=ENGINE),
            frozenset(SESSION_FREE_TOOLS),
            "run_query",
            {"sql": sql, "purpose": "synthetic severe symptoms"},
        )

    assert len(logs) == 1
    entry = logs[0]
    assert set(entry) <= {
        "event",
        "log_level",
        "tool_name",
        "duration_ms",
        "count",
        "outcome",
        "reason_code",
    }
    assert entry["tool_name"] == "run_query"
    assert entry["count"] == 1
    assert entry["outcome"] == "ok"
    rendered = repr(entry)
    for private in (sql, "synthetic headache", "synthetic severe symptoms", "symptom_name"):
        assert private not in rendered


@pytest.mark.safety("SAFE-29")
def test_unexpected_exceptions_do_not_leak_their_text(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake(*_args: Any) -> ToolOutput:
        raise RuntimeError(f"connection failed for {SYNTHETIC_MARKER}")

    monkeypatch.setattr(mcp_server, "execute_analysis_tool", fake)
    with capture_logs() as logs:
        result = mcp_server.call_analysis_tool(
            AnalysisAccess(engine=ENGINE), frozenset(SESSION_FREE_TOOLS), "describe_data", {}
        )

    assert result.is_error is True
    assert _body(result)["error"]["code"] == "tool_internal_error"
    assert SYNTHETIC_MARKER not in repr(result)
    assert SYNTHETIC_MARKER not in repr(logs)
    assert logs[0]["reason_code"] == "tool_internal_error"


# ---------------------------------------------------------------------------
# Access and startup
# ---------------------------------------------------------------------------


def test_default_access_uses_only_the_view_only_analyst_engine(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def text_engine() -> Engine:
        raise AssertionError("the text role must not be opened without the opt-in")

    monkeypatch.setattr(mcp_server, "get_analyst_engine", lambda: ENGINE)
    monkeypatch.setattr(mcp_server, "get_analyst_text_engine", text_engine)

    access = mcp_server.build_access(allow_text=False)

    assert access.engine is ENGINE
    assert access.text_engine is None
    assert access.allow_text is False
    assert access.model_session_factory is None


def test_text_flag_selects_the_text_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    def analyst_engine() -> Engine:
        raise AssertionError("text access uses only the text role")

    monkeypatch.setattr(mcp_server, "get_analyst_engine", analyst_engine)
    monkeypatch.setattr(mcp_server, "get_analyst_text_engine", lambda: TEXT_ENGINE)

    access = mcp_server.build_access(allow_text=True)

    assert access.text_engine is TEXT_ENGINE
    assert access.engine is None
    assert access.allow_text is True
    assert access.engine_for_query() is TEXT_ENGINE
    assert access.model_session_factory is None


@pytest.mark.parametrize(
    ("argv", "environment", "expected"),
    [
        (["--transport", "stdio"], None, False),
        (["--transport", "stdio", "--allow-text"], None, True),
        (["--transport", "stdio"], "true", True),
        (["--transport", "stdio"], "false", False),
    ],
)
def test_main_selects_text_access_only_on_explicit_opt_in(
    monkeypatch: pytest.MonkeyPatch,
    argv: list[str],
    environment: str | None,
    expected: bool,
) -> None:
    served: list[AnalysisAccess] = []
    if environment is not None:
        monkeypatch.setenv(mcp_server.ALLOW_TEXT_ENV, environment)
    monkeypatch.setattr(mcp_server, "get_analyst_engine", lambda: ENGINE)
    monkeypatch.setattr(mcp_server, "get_analyst_text_engine", lambda: TEXT_ENGINE)
    real_build_server = mcp_server.build_server

    def recording_build_server(access: AnalysisAccess) -> Any:
        served.append(access)
        return real_build_server(access)

    def serve_stdio(_server: object) -> None:
        return None

    monkeypatch.setattr(mcp_server, "build_server", recording_build_server)
    monkeypatch.setattr(mcp_server, "serve_stdio", serve_stdio)

    assert mcp_server.main(argv) == 0
    assert [access.allow_text for access in served] == [expected]


@pytest.mark.parametrize(
    ("argv", "variable"),
    [
        (["--transport", "stdio"], "HC_ANALYST_DATABASE_URL"),
        (["--transport", "http", "--allow-text"], "HC_ANALYST_TEXT_DATABASE_URL"),
    ],
)
def test_missing_engine_configuration_exits_with_an_error(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    argv: list[str],
    variable: str,
) -> None:
    _no_serving(monkeypatch)
    monkeypatch.setattr(mcp_server, "get_analyst_engine", lambda: None)
    monkeypatch.setattr(mcp_server, "get_analyst_text_engine", lambda: None)

    assert mcp_server.main(argv) == 2
    assert f"{variable} is not set" in capsys.readouterr().err


def test_invalid_settings_are_reported_without_values(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    class Probe(BaseModel):
        analyst_database_url: int

    with pytest.raises(ValidationError) as caught:
        Probe.model_validate({"analyst_database_url": SYNTHETIC_MARKER})
    assert SYNTHETIC_MARKER in str(caught.value)

    def invalid() -> Engine:
        raise caught.value

    _no_serving(monkeypatch)
    monkeypatch.setattr(mcp_server, "get_analyst_engine", invalid)

    assert mcp_server.main(["--transport", "stdio"]) == 2
    err = capsys.readouterr().err
    assert "analyst_database_url" in err
    assert SYNTHETIC_MARKER not in err


def test_unparseable_url_is_reported_without_its_value(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def invalid() -> Engine:
        raise ArgumentError(f"Could not parse SQLAlchemy URL from string '{SYNTHETIC_MARKER}'")

    _no_serving(monkeypatch)
    monkeypatch.setattr(mcp_server, "get_analyst_engine", invalid)

    assert mcp_server.main(["--transport", "stdio"]) == 2
    err = capsys.readouterr().err
    assert "HC_ANALYST_DATABASE_URL is not a valid database URL" in err
    assert SYNTHETIC_MARKER not in err


def test_invalid_text_opt_in_value_exits_with_an_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    _no_serving(monkeypatch)
    monkeypatch.setenv(mcp_server.ALLOW_TEXT_ENV, "sometimes")
    monkeypatch.setattr(mcp_server, "get_analyst_engine", lambda: ENGINE)

    assert mcp_server.main(["--transport", "stdio"]) == 2
    assert "HC_MCP_ALLOW_TEXT must be 'true' or 'false'" in capsys.readouterr().err


@pytest.mark.parametrize("host", [mcp_server.CONTAINER_BIND_HOST, "192.168.1.5", "health.example"])
def test_http_refuses_non_loopback_binding_outside_a_container(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str], host: str
) -> None:
    _no_serving(monkeypatch)
    monkeypatch.setattr(mcp_server, "get_analyst_engine", lambda: ENGINE)

    assert mcp_server.main(["--transport", "http", "--host", host]) == 2
    assert "--host must be a loopback address" in capsys.readouterr().err


def test_container_binding_is_limited_to_the_wildcard_address() -> None:
    mcp_server.validate_bind_host("127.0.0.1", container=False)
    mcp_server.validate_bind_host("::1", container=False)
    mcp_server.validate_bind_host(mcp_server.CONTAINER_BIND_HOST, container=True)
    with pytest.raises(mcp_server.ConfigurationError):
        mcp_server.validate_bind_host("192.168.1.5", container=True)


def test_http_defaults_to_loopback_port_8766(monkeypatch: pytest.MonkeyPatch) -> None:
    served: list[tuple[str, int]] = []

    def serve_http(_server: object, *, host: str, port: int) -> None:
        served.append((host, port))

    monkeypatch.setattr(mcp_server, "get_analyst_engine", lambda: ENGINE)
    monkeypatch.setattr(mcp_server, "serve_http", serve_http)
    wildcard = mcp_server.CONTAINER_BIND_HOST

    assert mcp_server.main(["--transport", "http"]) == 0
    assert mcp_server.main(["--transport", "http", "--host", wildcard, "--container"]) == 0
    assert served == [("127.0.0.1", 8766), (wildcard, 8766)]


# ---------------------------------------------------------------------------
# Host and Origin validation
# ---------------------------------------------------------------------------


def test_allow_lists_are_exact_local_names() -> None:
    hosts = ["localhost:8766", "127.0.0.1:8766", "host.docker.internal:8766"]
    origins = [
        "http://localhost:8766",
        "http://127.0.0.1:8766",
        "http://host.docker.internal:8766",
    ]
    assert mcp_server.allowed_hosts(PORT) == hosts
    assert mcp_server.allowed_origins(PORT) == origins
    settings = mcp_server.transport_security(PORT)
    assert settings.enable_dns_rebinding_protection is True
    assert settings.allowed_hosts == hosts
    assert settings.allowed_origins == origins
    assert mcp_server.MCP_PATH == "/mcp"


@pytest.fixture
def http_client() -> Iterator[TestClient]:
    server = mcp_server.build_server(AnalysisAccess(engine=ENGINE))
    app = mcp_server.build_http_app(server, port=PORT)
    with TestClient(app, base_url=f"http://localhost:{PORT}") as client:
        yield client


@pytest.mark.parametrize("host", mcp_server.allowed_hosts(PORT))
def test_allowed_hosts_without_origin_are_accepted(http_client: TestClient, host: str) -> None:
    response = http_client.post(
        mcp_server.MCP_PATH, json=INITIALIZE, headers={**MCP_HEADERS, "host": host}
    )
    assert response.status_code == 200
    assert '"serverInfo"' in response.text


@pytest.mark.parametrize("origin", mcp_server.allowed_origins(PORT))
def test_allowed_origins_are_accepted(http_client: TestClient, origin: str) -> None:
    response = http_client.post(
        mcp_server.MCP_PATH, json=INITIALIZE, headers={**MCP_HEADERS, "origin": origin}
    )
    assert response.status_code == 200


@pytest.mark.parametrize(
    "host",
    ["evil.example:8766", "localhost:9999", "localhost", "127.0.0.1.nip.io:8766"],
)
def test_foreign_hosts_are_rejected(http_client: TestClient, host: str) -> None:
    for path in (mcp_server.MCP_PATH, "/"):
        response = http_client.post(path, json=INITIALIZE, headers={**MCP_HEADERS, "host": host})
        assert response.status_code == 403


@pytest.mark.parametrize(
    "origin",
    ["http://evil.example", "http://localhost:3000", "https://localhost:8766", "null"],
)
def test_foreign_origins_are_rejected(http_client: TestClient, origin: str) -> None:
    response = http_client.post(
        mcp_server.MCP_PATH, json=INITIALIZE, headers={**MCP_HEADERS, "origin": origin}
    )
    assert response.status_code == 403


def test_sdk_enforces_the_same_allow_lists_behind_the_guard() -> None:
    server = mcp_server.build_server(AnalysisAccess(engine=ENGINE))
    app = server.streamable_http_app(
        streamable_http_path=mcp_server.MCP_PATH,
        transport_security=mcp_server.transport_security(PORT),
    )
    with TestClient(app, base_url=f"http://localhost:{PORT}") as client:
        host = client.post(
            mcp_server.MCP_PATH,
            json=INITIALIZE,
            headers={**MCP_HEADERS, "host": "evil.example:8766"},
        )
        origin = client.post(
            mcp_server.MCP_PATH,
            json=INITIALIZE,
            headers={**MCP_HEADERS, "origin": "http://evil.example"},
        )
    assert host.status_code in {403, 421}
    assert origin.status_code == 403
