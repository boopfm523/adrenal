# Local MCP server

HealthCurve includes a small [Model Context Protocol](https://modelcontextprotocol.io)
server. It lets a **local** model client use the same analysis tools as the in-app
analytical chat ([ADR-0036](adr/0036-local-model-analytical-chat.md)). The code is in
`src/healthcurve/mcp_server.py`.

## Local models only

> **Only local models may use HealthCurve private data.** Never connect this server to
> Claude Desktop, Claude Code, claude.ai, or any other cloud-backed model or client,
> not even for a quick test. Every tool result is private health data, and a cloud
> client would send it off the machine.

The intended client is [Open WebUI](https://openwebui.com) running in Docker on the
same Mac and talking to host-native Ollama. Any other client must use a local model
end to end, including any "helper" or title-generation model it runs.

## What it exposes

The server offers the catalog tools that need no owner session. Validation and
behavior are identical to chat:

| Tool | Purpose |
|---|---|
| `describe_data` | View catalog, columns, units, query conventions, example queries |
| `run_query` | One validated read-only `SELECT` over the `analytics` views |
| `clock_time_stats` | Circular mean, median, and range of local clock times |
| `event_window_stats` | Wearable samples before and after selected events |

`modeled_exposure` is **not** offered. It needs domain services over base tables, and
the MCP server has no owner session. Each result is returned as JSON text (`ok` plus
`data`, or `ok: false` plus a repairable `error`). Errors are also flagged as MCP tool
errors.

The server connects only through the view-only analyst role. PostgreSQL enforces the
boundary (see [analytics views](analytics-views.md)). A malformed or injected query can
at worst read the curated `analytics` views. It cannot read base tables or identity
data, and it cannot write.

## Enable the analyst role

Follow [Enabling the roles](analytics-views.md#enabling-the-roles) and set
`HC_ANALYST_DATABASE_URL` in `.env`. If the URL is not set, the server exits at startup
with an error naming the missing variable. It never falls back to a broader connection.

## Start it (HTTP, for Open WebUI)

PostgreSQL is not published to the host, so the HTTP server runs as an opt-in Compose
service on the internal network:

```bash
docker compose --profile mcp up -d mcp
```

The service publishes a single port, and only on the host loopback: `127.0.0.1:8766`.
Its endpoint is the streamable HTTP path `/mcp`:

| Client location | URL |
|---|---|
| Open WebUI (or another client) in Docker on the same Mac | `http://host.docker.internal:8766/mcp` |
| A local process on the Mac | `http://localhost:8766/mcp` or `http://127.0.0.1:8766/mcp` |

In Open WebUI, add it as an MCP server of the **Streamable HTTP** type with no
authentication, using the Docker URL above. The connection must be made by Open
WebUI's backend container, not directly from the browser. A browser request carries an
`Origin` such as `http://localhost:3000`, which the server rejects (see
[Security model](#security-model)). Choose a local Ollama model for the chat that uses
these tools.

Stop it with:

```bash
docker compose stop mcp
```

## Start it (stdio)

A local command-line client that speaks MCP over stdio can run a one-off container on
the same network:

```bash
docker compose --profile mcp run --rm -i --no-deps mcp \
  python -m healthcurve.mcp_server --transport stdio
```

`--no-deps` avoids restarting PostgreSQL; it must already be running. Logs go to
stderr, because stdout is the protocol stream.

## Free-text access (opt-in)

By default the server uses `healthcurve_analyst`, which cannot read the
`analytics_text` views (diary, life events, notes). To let a local client read free
text:

- Compose: set `HC_MCP_ALLOW_TEXT=true` and `HC_ANALYST_TEXT_DATABASE_URL` in `.env`,
  then run `docker compose --profile mcp up -d --force-recreate mcp`.
- Command line: pass `--allow-text`.

With text enabled, the server connects only as `healthcurve_analyst_text`. If that URL
is not set, it exits at startup. Any other `HC_MCP_ALLOW_TEXT` value than `true` or
`false` is a startup error. Set it back to `false` and recreate the service to turn
text access off again.

## Security model

There is **no token or secret**. A token would add a credential to manage without
addressing the concrete risk for a loopback-only service
([ADR-0036](adr/0036-local-model-analytical-chat.md)). Protection comes from:

1. **Loopback-only publication.** Compose publishes `127.0.0.1:8766:8766` and nothing
   else, and the server is never exposed on the LAN, tailnet, or internet.
   `scripts/check_compose_topology.py` rejects any other binding. Inside the container
   the process binds `0.0.0.0` because that is the container's own interface and Docker
   forwards the loopback-published port to it. `--container` must be passed for that
   bind, and outside a container the server refuses a non-loopback `--host`.
2. **Host header allow-list.** Only `localhost:8766`, `127.0.0.1:8766`, and
   `host.docker.internal:8766` are accepted. This defeats DNS rebinding, where a web
   page makes a hostile name resolve to 127.0.0.1.
3. **Origin header allow-list.** A request with an `Origin` is accepted only from
   `http://localhost:8766`, `http://127.0.0.1:8766`, or
   `http://host.docker.internal:8766`. This blocks cross-site requests from web pages
   in a browser. Requests without an `Origin`, as non-browser clients send, are
   accepted.

Both allow-lists are exact and enforced twice. An outer guard answers `403` on every
path. Behind it, the MCP SDK's own DNS-rebinding protection is configured with the same
lists. The port is part of each allowed value, so publishing on a different host port
requires changing the server's `--port` to match.

Accepted consequences:

- Any local process on the Mac, and any container that can reach
  `host.docker.internal`, can call the tools without credentials. This is acceptable
  under the private single-owner threat model and is the reason the server must only
  run on the owner's own machine.
- The database role, not the MCP layer, is the data boundary. Text stays unreadable
  unless explicitly enabled.

Logging follows SAFE-29 (threat model C9/C14): each call logs only the tool name,
duration, row count, outcome, and error code. SQL, arguments, results, and exception
text are never logged or returned. HTTP access logs are disabled.
