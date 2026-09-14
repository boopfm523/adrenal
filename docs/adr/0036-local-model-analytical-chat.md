# ADR-0036: Local-model analytical chat over curated read-only views

- Status: Accepted
- Date: 2026-09-14
- Supersedes in part: [ADR-0025](0025-private-health-data-chatbot.md)

## Context

ADR-0025 gave the owner a first-party chat over ten fixed, allow-listed domain tools.
In practice it cannot answer ordinary analytical questions such as "what was my
average bedtime over the last 30 days", "how many minutes after waking did I take my
medication on average", or "what were my metrics in the hour before and after each
symptom in the last two weeks". The causes are structural:

- regex shortcut routes answer some questions with fixed templates, and regex date
  parsing overrides the model's chosen range (unspecified ranges become 14 days);
- a one-shot JSON planner selects from ten tools with fixed shapes and row caps, and
  the Ollama adapter supports neither native tool calling nor multi-turn tool results;
- the validator rejects any number that does not appear literally in a tool result,
  so no tool-less derivation is possible, and no tool computes most useful statistics;
- the evaluation set has five cases and does not test analytical correctness.

The owner wants to "talk to the data": ask open-ended questions over any time period
and have the system retrieve, analyze, and answer. The owner decided on 2026-09-14:

1. Only local Ollama models may read private health data. No cloud model and no
   cloud-backed client (including Claude Desktop, Claude Code, or claude.ai) may be
   connected. Development, tests, and evaluations use synthetic data only.
2. The data source is the private live database, not the public static export.
3. Diary, life-event, and note free text is opt-in per conversation.
4. Thinking mode is enabled by default for chat, trading latency for accuracy.
5. A local MCP server exposes the same tools. It needs no token.

## Decision

### Analytical data layer

A new `analytics` schema contains curated, read-only views designed for analysis
rather than for UI clients. Views:

- expose only current revisions (a row is excluded when another row supersedes it) and
  exclude voided doses;
- present UTC instants together with local date, local clock time, IANA timezone, and
  explicit units;
- keep categories explicit: recorded facts, physician-approved plans (in separately
  named plan views), and nothing AI-generated;
- pre-derive values that are error-prone to reconstruct, following existing domain
  rules: overnight sleep versus naps, final wake per local day, dose minutes after wake,
  daily wearable totals versus provider samples, and local-day boundaries across DST;
- omit owner identifiers, credentials, exact location, raw provider payloads, and
  document bytes; and
- place every free-text column (diary, life-event, symptom/dose/episode notes,
  descriptions) in a separate `analytics_text` schema.

Modeled cortisol/exposure values are computed only in Python. They are available
through a helper tool and are labeled as modeled analysis, never as recorded facts.

### Database-enforced access

Three roles implement the boundary in PostgreSQL rather than in model behavior:

- `healthcurve_analytics_owner` (`NOLOGIN`) owns the views and holds only the SELECT
  privileges on base `fact`, `plan`, and `ops` tables that the views require. It has no
  access to `identity`.
- `healthcurve_analyst` (`LOGIN`) has USAGE and SELECT on `analytics` only. It holds no
  privilege on any base table, any other schema, or any write operation.
- `healthcurve_analyst_text` (`LOGIN`) additionally reads `analytics_text`. It is used
  only when the owner enables text for the conversation.

Views use PostgreSQL's default owner-privilege semantics, not `security_invoker`, so
the analyst roles never need base-table access. New volumes create the roles in the
init scripts. The existing database receives an idempotent, documented setup path.
Connection URLs come from `HC_ANALYST_DATABASE_URL` and `HC_ANALYST_TEXT_DATABASE_URL`.
HealthCurve is single-owner (a second owner cannot be created), so views do not filter
by owner. If that invariant ever changes, this decision must be revisited before a
second owner is allowed.

### Analysis tools

The model uses one versioned catalog, shared by chat and MCP:

- `describe_data`: views, columns, units, semantics, missingness rules, and example
  queries, generated from the same source of truth as the migrations.
- `run_query`: model-authored SQL, accepted only when a PostgreSQL-dialect parser
  proves it is exactly one `SELECT`/`WITH` statement over `analytics` (or
  `analytics_text` when enabled) relations using allow-listed functions. It executes on
  an analyst role in a `READ ONLY` transaction with a statement timeout, a row cap, and
  a result-size cap. Parse, validation, and database errors are returned to the model
  in sanitized form so it can correct the query.
- `clock_time_stats`: circular mean, median, and range of local clock times (bedtime,
  wake, dose times) with sample size and missingness.
- `event_window_stats`: wearable aggregates in configurable windows before and after
  each selected event, per event and overall. Missing samples are never zero.
- `modeled_exposure`: values from the default Python exposure model, labeled as modeled.

The database and deterministic helpers perform all arithmetic. The model chooses what
to compute and explains results.

### Orchestration

Chat uses native Ollama tool calling with multi-turn tool results. Regex shortcut
routes, the one-shot planner, and regex date overrides are removed. The model states
the time scope it used. When the owner gives none, it picks a reasonable scope and
says so.

- The default chat model is `HC_OLLAMA_MODEL`, the model the evaluation sets gate. The
  owner may choose another model per conversation, but only from models installed in
  the local Ollama that report tool-calling support; Ollama cloud or remote models are
  never listed or accepted. The choice is re-checked when each answer runs, thinking is
  requested only from models that support it, and each answer's provenance records the
  model name and digest. Non-default models are not evaluated, and loading one can
  unload the default model from memory; the chat UI says so.
- Thinking is enabled by default (`HC_CHAT_THINKING=true`). Thinking text is transient
  C9 data: never persisted, logged, or shown.
- The context window comes from configuration. Tool results are budgeted and truncated
  with explicit row counts and truncation flags.
- Tool rounds, total tool calls, per-call time, and whole-run time are bounded.
  Cancellation remains available.
- The final answer is submitted through a strict schema. Deterministic validation
  rejects: any numeric health claim not grounded in tool results (numbers and dates from
  the owner's question and the stated scope excepted). Grounded restatements are
  accepted: parts of dates and timestamps, 12-hour forms of clock hours, decimals rounded
  to fewer places, and day counts within a queried date range (the range length, or that
  length minus a count the tool returned). A rejected draft gets up to two repair turns
  with the offending numbers listed. The validator also rejects dose, schedule, taper, or stress
  guidance (SAFE-17); emergency authorship (SAFE-22); causal claims from association
  (SAFE-25); and missing disclosure of material missingness (SAFE-26).
- Each completed answer stores tool names and versions, validated arguments including
  SQL text, result fingerprints, time scope, model identity, and prompt and schema
  versions. Result bodies are not stored. Stale-answer detection re-executes the stored
  calls and compares fingerprints.

### Local MCP server

An MCP server exposes `describe_data`, `run_query`, and the helpers through the same
module and roles:

- stdio for local command-line clients; and
- streamable HTTP bound to `127.0.0.1` only, rejecting requests whose `Host` or `Origin`
  is not an allow-listed local name (`localhost`, `127.0.0.1`, and
  `host.docker.internal` for the Dockerized local Open WebUI). This prevents DNS
  rebinding and cross-site browser requests without a secret to manage.

The server is not published on the tailnet or any public interface. Text access
requires starting it explicitly with the text role. Its documentation states that only
local-model clients may be connected. Logs contain tool names, durations, row counts,
and error codes only.

Implemented in [docs/local-mcp-server.md](../local-mcp-server.md).

### Retained from ADR-0025

Conversation storage, retention, deletion, export, durable queued runs and their state
machine, stale-answer marking, privacy and observability rules, accessibility, and the
AI-analysis labeling remain as decided in ADR-0025.

## Consequences

Positive:

- Open analytical questions over any period become answerable, with computation done
  by PostgreSQL or deterministic helpers rather than by the model.
- Access limits are enforced by database privileges, so a malformed or injected query
  can at worst read curated analytics views. It cannot read base tables, identity data,
  or text without consent, and cannot write.
- One tool layer serves the in-app chat and local MCP clients.
- A numeric evaluation set makes model, prompt, and view changes measurable.

Negative / costs:

- Model-authored SQL is less predictable than fixed tools. It is mitigated by
  validation, curated views, helpers for error-prone computations, repair loops, and
  the evaluation gate.
- Thinking mode increases answer latency, possibly to minutes.
- Three roles and two connection URLs add deployment configuration, and the view
  catalog must be kept in sync with domain rules.
- A local process on the Mac can call the loopback MCP server without credentials.
  This is accepted under the private single-owner threat model.

## Alternatives considered

**Expose the public static JSON export.** Rejected: it omits labs, notes, and other
private data, appears only after a delayed eligibility gate, and leaves arithmetic
over raw JSON to the model.

**Pass raw records to the model.** Rejected: it does not fit the local context window
and local models compute averages and time differences unreliably.

**Keep adding fixed tools.** Rejected: every new question shape would need new code,
which is the failure mode of ADR-0025.

**Use a cloud model.** Rejected by the owner.

**`security_invoker` views.** Rejected: analyst roles would need base-table
privileges, so a validator bypass could read base tables directly.

**Token-protected MCP.** Rejected as unnecessary complexity for a loopback-only server.
Host and Origin validation covers the concrete browser risk.
