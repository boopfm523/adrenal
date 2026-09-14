"""Local MCP server exposing the analysis tool catalog (ADR-0036).

Only LOCAL models may use HealthCurve private data. Never connect this server to Claude
Desktop, Claude Code, claude.ai, or any other cloud-backed client
(docs/local-mcp-server.md).

The server offers the catalog tools that need no owner session -- ``describe_data``,
``run_query``, ``clock_time_stats``, and ``event_window_stats`` -- through the same
:func:`healthcurve.analysis.tools.execute_analysis_tool` the in-app chat uses, so
validation and behavior are identical. It connects only as the view-only analyst role;
text access requires starting it explicitly with the text role.

Transports:

* ``stdio`` for a local command-line client; and
* ``http`` (streamable HTTP at ``/mcp``) for the Dockerized local Open WebUI.

There is no token. The HTTP transport is protected by loopback-only publication and by
Host and Origin allow-lists that stop DNS rebinding and cross-site browser requests.

Like ``cli.py`` and ``worker.py``, this entry point sits outside the layered domain
stack; domain modules must never import it (``.importlinter``).
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import time
from collections.abc import Mapping, Sequence
from typing import Any, Final

import anyio
import mcp.types as types
import structlog
import uvicorn
from anyio import to_thread
from mcp.server import Server, ServerRequestContext
from mcp.server.stdio import stdio_server
from mcp.server.transport_security import TransportSecuritySettings
from pydantic import ValidationError
from sqlalchemy.exc import ArgumentError
from starlette.datastructures import Headers
from starlette.responses import PlainTextResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from healthcurve.analysis.catalog import CATALOG_VERSION
from healthcurve.analysis.tools import (
    AnalysisAccess,
    ToolOutput,
    execute_analysis_tool,
    tool_definitions,
)
from healthcurve.db import get_analyst_engine, get_analyst_text_engine
from healthcurve.logging import configure_logging, get_logger

log = get_logger(__name__)

SERVER_NAME: Final = "healthcurve-analysis"
DEFAULT_HOST: Final = "127.0.0.1"
DEFAULT_PORT: Final = 8766
#: The streamable HTTP endpoint path (the SDK default, stated explicitly).
MCP_PATH: Final = "/mcp"
#: Compose opt-in for text access; equivalent to ``--allow-text``.
ALLOW_TEXT_ENV: Final = "HC_MCP_ALLOW_TEXT"
#: Local names a client may use to reach the server. ``host.docker.internal`` is how
#: the Dockerized local Open WebUI reaches a port published on the host loopback.
LOCAL_HOST_NAMES: Final = ("localhost", "127.0.0.1", "host.docker.internal")
#: Addresses the server may bind outside a container.
LOOPBACK_BIND_HOSTS: Final = frozenset({"127.0.0.1", "::1", "localhost"})
#: Inside a container only, and only because Compose publishes the port on 127.0.0.1.
CONTAINER_BIND_HOST: Final = "0.0.0.0"  # noqa: S104 - see --container

INSTRUCTIONS: Final = (
    "HealthCurve analysis tools over curated, read-only views of one person's private "
    "health record. Call describe_data first. Do all arithmetic with run_query or the "
    "helpers, state the date range used, and report how many rows each result is based "
    "on. Recorded facts, physician-approved plans, and modeled analysis are different "
    "categories. This is not medical advice and never a basis for changing a dose."
)

_UNKNOWN_TOOL: Final = "unknown"


class ConfigurationError(Exception):
    """A startup problem whose message is safe to print (never contains a value)."""


# ---------------------------------------------------------------------------
# Access
# ---------------------------------------------------------------------------


def allow_text_from_environment(environ: Mapping[str, str]) -> bool:
    raw = environ.get(ALLOW_TEXT_ENV, "").strip().lower()
    if raw in {"", "false"}:
        return False
    if raw == "true":
        return True
    raise ConfigurationError(f"{ALLOW_TEXT_ENV} must be 'true' or 'false'.")


def build_access(*, allow_text: bool) -> AnalysisAccess:
    """View-only analyst access; the text role only when explicitly allowed.

    There is no fallback to a broader role and no owner session, so tools that need
    domain services over base tables (``modeled_exposure``) are not offered.
    """

    variable = "HC_ANALYST_TEXT_DATABASE_URL" if allow_text else "HC_ANALYST_DATABASE_URL"
    try:
        engine = get_analyst_text_engine() if allow_text else get_analyst_engine()
    except ValidationError as exc:
        # str(exc) would include input values, which can be connection URLs.
        fields = sorted({".".join(str(part) for part in error["loc"]) for error in exc.errors()})
        detail = ", ".join(fields) if fields else "settings"
        raise ConfigurationError(
            f"HealthCurve configuration is invalid ({detail}); values are not shown."
        ) from None
    except ArgumentError:
        raise ConfigurationError(f"{variable} is not a valid database URL.") from None
    if engine is None:
        raise ConfigurationError(
            f"{variable} is not set. The MCP server connects only as the view-only analyst "
            "role; see docs/analytics-views.md to enable it."
        )
    if allow_text:
        return AnalysisAccess(
            engine=None, text_engine=engine, allow_text=True, model_session_factory=None
        )
    return AnalysisAccess(engine=engine, model_session_factory=None)


# ---------------------------------------------------------------------------
# MCP server
# ---------------------------------------------------------------------------


def mcp_tools(access: AnalysisAccess) -> list[types.Tool]:
    """The catalog tools this access can run, as MCP tool definitions."""

    tools: list[types.Tool] = []
    for definition in tool_definitions(access):
        function = definition["function"]
        tools.append(
            types.Tool(
                name=function["name"],
                description=function["description"],
                input_schema=function["parameters"],
            )
        )
    return tools


def _row_count(output: ToolOutput) -> int | None:
    data = output.data or {}
    count = data.get("row_count")
    if isinstance(count, int):
        return count
    events = data.get("events")
    return len(events) if isinstance(events, list) else None


def call_analysis_tool(
    access: AnalysisAccess,
    offered: frozenset[str],
    name: str,
    arguments: Mapping[str, Any] | None,
) -> types.CallToolResult:
    """Run one offered catalog tool and log only operational metadata (SAFE-29)."""

    started = time.monotonic()
    if name not in offered:
        output = ToolOutput(
            tool_name=_UNKNOWN_TOOL,
            tool_version=_UNKNOWN_TOOL,
            ok=False,
            error_code="tool_unknown",
            error_message=f"Unknown tool. Available tools: {', '.join(sorted(offered))}.",
        )
    else:
        try:
            output = execute_analysis_tool(access, name, dict(arguments or {}))
        except Exception:
            # Exception text can carry SQL or parameters; neither the log nor the
            # client receives it.
            output = ToolOutput(
                tool_name=name,
                tool_version=_UNKNOWN_TOOL,
                ok=False,
                error_code="tool_internal_error",
                error_message="The tool failed unexpectedly. Try again later.",
            )
    fields: dict[str, Any] = {
        "tool_name": name if name in offered else _UNKNOWN_TOOL,
        "duration_ms": round((time.monotonic() - started) * 1000, 1),
        "outcome": "ok" if output.ok else "error",
    }
    count = _row_count(output)
    if count is not None:
        fields["count"] = count
    if output.error_code is not None:
        fields["reason_code"] = output.error_code
    log.info("mcp tool call", **fields)
    return types.CallToolResult(
        content=[types.TextContent(text=output.as_model_content())],
        is_error=not output.ok,
    )


def build_server(access: AnalysisAccess) -> Server[Any]:
    if access.model_session_factory is not None:
        raise ValueError("the MCP server must not receive an owner session factory")
    tools = mcp_tools(access)
    offered = frozenset(tool.name for tool in tools)

    async def list_tools(
        _ctx: ServerRequestContext[Any], _params: types.PaginatedRequestParams | None
    ) -> types.ListToolsResult:
        return types.ListToolsResult(tools=list(tools))

    async def call_tool(
        _ctx: ServerRequestContext[Any], params: types.CallToolRequestParams
    ) -> types.CallToolResult:
        # Database work is synchronous; keep it off the event loop.
        return await to_thread.run_sync(
            call_analysis_tool, access, offered, params.name, params.arguments
        )

    return Server(
        SERVER_NAME,
        version=CATALOG_VERSION,
        instructions=INSTRUCTIONS,
        on_list_tools=list_tools,
        on_call_tool=call_tool,
    )


# ---------------------------------------------------------------------------
# HTTP transport security
# ---------------------------------------------------------------------------


def allowed_hosts(port: int) -> list[str]:
    return [f"{name}:{port}" for name in LOCAL_HOST_NAMES]


def allowed_origins(port: int) -> list[str]:
    return [f"http://{name}:{port}" for name in LOCAL_HOST_NAMES]


def transport_security(port: int) -> TransportSecuritySettings:
    """The SDK's DNS-rebinding protection with exact (no wildcard) allow-lists."""

    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed_hosts(port),
        allowed_origins=allowed_origins(port),
    )


class LocalRequestGuard:
    """Reject any request whose Host or Origin is not an allow-listed local name.

    The SDK performs the same check on the MCP endpoint (answering 421 for a bad Host).
    This outer guard covers every path and scope with a uniform 403, so protection
    does not depend on SDK routing details. A request without an Origin header -- as
    sent by non-browser clients such as Open WebUI's backend -- is accepted.
    """

    def __init__(self, app: ASGIApp, *, port: int) -> None:
        self.app = app
        self._hosts = frozenset(allowed_hosts(port))
        self._origins = frozenset(allowed_origins(port))

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)
        hosts = headers.getlist("host")
        origins = headers.getlist("origin")
        reason: str | None = None
        if len(hosts) != 1 or hosts[0] not in self._hosts:
            reason = "host_not_allowed"
        elif len(origins) > 1 or (origins and origins[0] not in self._origins):
            reason = "origin_not_allowed"
        if reason is None:
            await self.app(scope, receive, send)
            return
        log.warning("mcp request rejected", reason_code=reason, status_code=403)
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        await PlainTextResponse("Forbidden", status_code=403)(scope, receive, send)


def build_http_app(server: Server[Any], *, port: int) -> ASGIApp:
    app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        transport_security=transport_security(port),
    )
    return LocalRequestGuard(app, port=port)


def validate_bind_host(host: str, *, container: bool) -> None:
    if host in LOOPBACK_BIND_HOSTS:
        return
    if container and host == CONTAINER_BIND_HOST:
        return
    try:
        if ipaddress.ip_address(host).is_loopback:
            return
    except ValueError:
        pass
    raise ConfigurationError(
        "--host must be a loopback address. Binding 0.0.0.0 is allowed only with "
        "--container, inside a container whose port is published on 127.0.0.1."
    )


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


async def _serve_stdio(server: Server[Any]) -> None:
    async with stdio_server() as (read_stream, write_stream):
        await server.run(read_stream, write_stream, server.create_initialization_options())


def serve_stdio(server: Server[Any]) -> None:
    anyio.run(_serve_stdio, server)


def serve_http(server: Server[Any], *, host: str, port: int) -> None:
    uvicorn.run(
        build_http_app(server, port=port),
        host=host,
        port=port,
        log_level="warning",
        # Access lines add nothing an operator needs and are not allow-list redacted.
        access_log=False,
        proxy_headers=False,
        server_header=False,
    )


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="python -m healthcurve.mcp_server",
        description=(
            "Local MCP server for HealthCurve analysis tools. Local-model clients only; "
            "never connect Claude or any cloud-backed client."
        ),
    )
    parser.add_argument("--transport", choices=("stdio", "http"), required=True)
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP bind address (loopback).")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP port.")
    parser.add_argument(
        "--container",
        action="store_true",
        help=(
            "Allow --host 0.0.0.0 inside a container whose port Compose publishes only on "
            "127.0.0.1."
        ),
    )
    parser.add_argument(
        "--allow-text",
        action="store_true",
        help=(
            "Use the analyst text role so free-text views are readable "
            f"(also enabled by {ALLOW_TEXT_ENV}=true)."
        ),
    )
    args = parser.parse_args(argv)
    if not 1 <= args.port <= 65_535:
        parser.error("--port must be between 1 and 65535")
    return args


def configure_process_logging() -> None:
    """Redacted JSON logs on stderr; stdout is the stdio transport's wire."""

    configure_logging(json_output=True)
    structlog.configure(logger_factory=structlog.PrintLoggerFactory(file=sys.stderr))


def main(argv: Sequence[str] | None = None) -> int:
    args = parse_args(argv)
    configure_process_logging()
    try:
        allow_text = args.allow_text or allow_text_from_environment(os.environ)
        if args.transport == "http":
            validate_bind_host(args.host, container=args.container)
        access = build_access(allow_text=allow_text)
    except ConfigurationError as exc:
        print(f"healthcurve mcp server: {exc}", file=sys.stderr)
        return 2
    server = build_server(access)
    log.info(
        "mcp server starting",
        outcome="text_enabled" if allow_text else "text_disabled",
        count=len(mcp_tools(access)),
    )
    if args.transport == "stdio":
        serve_stdio(server)
    else:
        serve_http(server, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
