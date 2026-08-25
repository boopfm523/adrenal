# ADR-0025: Private health-data chatbot uses bounded read-only domain tools

**Status:** Accepted — 2026-08-16

## Context

The owner wants a first-party Chat tab that can answer conversational questions about
the HealthCurve record. Useful questions cross several domains: recorded doses and the
applicable approved plan, symptoms and episodes, labs, Garmin observations, reports,
and deterministic HealthCurve analytics. A useful conversation also needs continuity
across messages rather than treating every question as an isolated prompt.

Giving a model database credentials, arbitrary SQL, or a generic record API would make
the model responsible for ownership filtering, query bounds, missingness, provenance,
and medical-safety rules. Those are deterministic application responsibilities. It
would also make prompt injection in a diary entry or imported note capable of changing
what the model queries. The existing daily analysis in ADR-0020 demonstrates the safer
pattern: deterministic code projects bounded source data, while Ollama only interprets
validated results.

Chat requests can outlive an ordinary browser request. The configured private model
may take tens of seconds, time out, restart, or return malformed structured output.
The product must expose progress and recovery rather than leaving a button apparently
inactive. Conversation text and answers are themselves sensitive health information
and require explicit retention, export, deletion, redaction, and stale-data behavior.

The owner approved autonomous product-quality additions within this epic and asked
that MCP be excluded from the current implementation.

## Decision

### First-party modular-monolith feature

HealthCurve adds a `chat` application domain inside the existing modular monolith and
an authenticated Chat tab in the React application. No new public service or listener
is introduced. The browser communicates only with the authenticated HealthCurve API;
all model access continues through the host-native private Ollama adapter in ADR-0017.

The `chat` domain owns conversation lifecycle, bounded context selection, tool
orchestration, source fingerprints, and the API contract. It consumes public domain
interfaces for analytics, reports, labs, medications, episodes, and events. It does
not import or expose their write paths. The `ai` domain remains the sole Ollama adapter
and model-output validation boundary.

MCP, arbitrary SQL, general-purpose database browsing, external chat clients, cloud
inference, voice, and record mutation are not part of this decision.

### Stored conversation model

Chat working state belongs to the `ai` category and schema; it is neither a recorded
fact nor a physician-approved plan. The initial schema has:

- `ai.chat_conversation`: owner, title, sensitive-text preference, created/updated
  time, last-message time, and optional deletion/retention metadata.
- `ai.chat_message`: conversation, owner, `user` or `assistant` role, body, lifecycle
  state, sequence, generated time, and assistant-only model/prompt/schema/tool
  provenance, source manifest, source scope, and fingerprint.
- `ai.chat_tool_execution`: assistant-message/run identity, approved tool name and
  version, validated arguments, outcome, duration, result fingerprint, source
  manifest, and redacted error code. Raw tool results are transient and are not copied
  into this table.

Only owner-visible user and assistant turns are retained. System prompts, full
assembled model prompts, raw Ollama responses, and transient tool-result bodies remain
class C9 and are not persisted or logged. User turns and assistant answers are class
C14 as defined by the threat model.

Conversation rows are retained until the owner deletes them by default. The UI offers
new, rename, delete, and delete-all controls; a future owner-selected automatic expiry
may be added without changing the data model. Deletion removes the conversation,
messages, and tool-execution metadata in one transaction, while the audit record of
the deletion remains. Account deletion and complete private export include chat data.
Physician reports exclude chat by default and never treat a chat answer as a fact.

### Bounded conversational memory

The complete owner-visible conversation remains readable, but each model request gets
bounded working context:

- at most the most recent 12 user/assistant turns;
- at most 24,000 UTF-8 characters of retained turns;
- an optional rolling conversation summary capped at 3,000 characters; and
- the current user question and current tool results.

The rolling summary may retain topics, terminology, date scopes, and unresolved
questions. It is AI working context, not evidence. It may never be used as the source
of a health value or numeric claim. Any health claim from an earlier turn must be
re-read through a domain tool before it can be repeated as current. A malformed or
oversized summary is discarded and rebuilt safely.

### Read-only domain tools

The model can request only versioned tools from an allow-list. It never supplies SQL,
table names, owner IDs, or database connection details. The API injects the
authenticated owner and validates every argument. Initial tools are:

1. `get_data_availability` — domain availability and missingness for a date range.
2. `get_daily_healthcurve` — the selected-day fingerprinted projection from ADR-0020.
3. `search_timeline` — bounded current facts by date, record type, and cursor.
4. `get_medication_context` — approved plan versions and recorded doses kept distinct.
5. `get_symptom_episode_context` — symptoms, stress episodes, and linked recorded facts.
6. `get_wearable_context` — daily summaries and bounded intraday buckets; missing is
   never converted to zero.
7. `get_preceding_health_context` — an explicit event-centered anchor with a bounded
   preceding-hours window, modeled curve position, recorded weather and sleep context,
   wearable comparisons, and bounded prior symptom or stress-episode anchors.
8. `get_lab_trends` — normalized lab trends with original values, units, ranges, and
   source provenance.
9. `compare_periods` — deterministic differences, counts, distributions, and supported
   correlations with sample size, method, timezone, and missingness.
10. `get_report_snapshot_context` — bounded metadata and deterministic contents of
   owner-selected immutable report snapshots, excluding report artifacts and AI
   sections unless explicitly requested.

Defaults are a 30-day range and 50 sparse rows. A single tool call may span at most
366 local calendar days, return at most 200 sparse rows, and include at most 2,000
dense buckets after deterministic aggregation. Pagination is explicit. Exact location,
credentials, password/session data, raw documents, and unrelated free text are never
tool output. Sensitive diary and life-event text is excluded unless the owner enables
it for the conversation or explicitly includes it for the current request.

All calculations, including correlations and numeric comparisons, are produced by
deterministic domain code. The model may select a supported calculation and explain
its returned result. It may not calculate or invent an unsupported number.

### Operation-scoped database roles

Tool execution uses a dedicated operation-scoped read-only database connection. Its
role has only the SELECT privileges needed by the tool catalog and no access to
identity credentials or integration secrets. Each tool transaction is additionally
declared read-only. It cannot INSERT, UPDATE, DELETE, execute arbitrary functions, or
change schema. Owner-scope predicates remain mandatory and are authorization-tested.

Conversation creation, owner-authored messages, rename, and delete are human API
operations using the normal application role. Persisting validated assistant messages
and tool metadata uses the restricted AI role, which retains no write privileges on
`fact` or `plan`. Role selection follows ADR-0009: it is per operation, not per process.

### Validated orchestration

One response run has a strict state machine:

`queued -> planning -> reading -> generating -> completed`

and terminal `cancelled`, `unavailable`, `timed_out`, `invalid`, or `failed` states.
Posting the user message and enqueueing the response run are one transaction through
the durable queue in ADR-0004. A request is idempotent by conversation and client
message ID. Browser refresh, navigation, or API restart does not lose the accepted
message. The UI polls the durable run initially; streaming may be added over the same
state machine without changing persistence.

The planner receives the user question and bounded conversation context and returns a
strict schema containing only allow-listed tool calls. Health text retrieved by a tool
is delimited as untrusted data and cannot introduce new tool calls or alter system
instructions. The server permits at most three planning rounds and eight total tool
calls per response. Per-tool and whole-run timeouts, cancellation, rate limits, and
output-size limits are mandatory.

The answer step receives only validated tool results. Its strict schema contains the
answer, missingness statement, source references, correlation caution when applicable,
and refusal reason when required. Deterministic validation rejects:

- a source reference not present in a tool result;
- a numeric health claim absent from deterministic tool output;
- contradictory missingness;
- medication or emergency guidance prohibited by SAFE-17 and SAFE-22;
- an answer that presents association as causation; or
- output that attempts to create or alter a fact or plan.

Invalid output is never partially displayed or saved. A typed, actionable failure is
shown with retry. The recorded facts, plans, and prior conversation remain unchanged.

### Provenance and stale answers

Every completed assistant message stores the union of source record IDs, explicit date
scope, tool names/versions, deterministic input fingerprints, model name/digest, prompt
version, schema version, and generation time. The primary answer stays readable; the
UI exposes these details in an expandable **Data used** region.

Source fingerprints include current record revision identity, applicable plan version,
wearable-summary revision, and deterministic model version. When a visible answer is
read, HealthCurve compares its fingerprint with the current projection for the same
scope. Late Garmin data, a correction, a plan-version change, or an analytics-model
change marks the answer **Data changed since this answer** and offers regeneration. It
does not silently rewrite history.

### Product UX and accessibility

The Chat tab provides conversation history on desktop and a drawer on iPad/iPhone,
suggested questions based only on available domains, a multiline composer, explicit
send/cancel/retry controls, progressive run status, and an optional sensitive-text
toggle. Keyboard focus moves to the new answer or actionable error. Screen readers
receive status through a polite live region. Touch targets meet the existing mobile
standards, and message content is sanitized as untrusted text.

Completed prior turns are condensed by default while the latest turn remains open.
Native-button controls expand or collapse one prior turn or all prior turns, retain
their state during the browser session, and expose `aria-expanded`/`aria-controls`.

Each assistant answer is labeled **AI HealthCurve analysis**, includes the generation
time, and carries the non-diagnostic/correlation boundary when relevant. Category is
not communicated by color alone. Emergency information and all record-entry workflows
remain independent of Chat and Ollama.

### Privacy, observability, and evaluation

Health-bearing responses use `Cache-Control: no-store`. Logs and telemetry contain
only run ID, owner-scoped opaque IDs, tool names, counts, state transitions, latency,
timeout/error codes, prompt/schema/tool versions, and model identity. They never contain
questions, answers, health values, source text, exact location, or tool-result bodies.

Operational signals include queue age, total duration, tool duration/outcome, model
outcome, invalid-output reason, cancellation, and stale-answer count. Repeated failure
is visible in Data quality without health content.

The synthetic evaluation contract in `docs/chatbot-evaluation.md` is a release gate for
changes to the model, prompt, schema, tool catalog, or orchestration policy.

## Consequences

Positive:

- The model can answer cross-domain questions without becoming a database principal or
  a source of computed facts.
- Durable runs and explicit states avoid silent buttons and survive browser/API churn.
- A reusable tool layer supports both chat quality and deterministic testing without a
  new public service.
- Per-answer fingerprints make late Garmin data and corrected facts visible.
- Conversation continuity is useful while model context and retained prompts remain
  bounded and owner-controlled.

Negative / costs:

- Conversation persistence adds a new high-sensitivity retention surface and requires
  export, deletion, backup, redaction, and shared-device UX.
- A planner/tool/answer loop is more code than one large prompt. That cost buys bounded
  access, deterministic calculations, observable failure, and testability.
- A dedicated read role and two operation-scoped sessions add deployment configuration.
- Strict numeric and source validation may reject a useful answer. Visible retry and
  deterministic fallback are preferred over displaying an unsupported claim.

## Alternatives considered

**Give Ollama a database connection or arbitrary SQL tool.** Rejected because ownership,
query bounds, missingness, and write safety would depend on model behavior.

**Expose every REST endpoint as a generic model tool.** Rejected because record APIs
include write workflows and response shapes designed for UI clients rather than bounded
model context. Domain tools reuse the same services but define a smaller read contract.

**Use one giant prompt containing the owner record.** Rejected because it does not
scale, obscures missingness and provenance, increases prompt-injection exposure, and
cannot answer late-data questions reliably.

**Store no conversation history.** Rejected because follow-up questions would lose
their referents and repeat the Telegram continuity problem. Full history remains
owner-visible while model context is bounded.

**Run every response synchronously in the API request.** Rejected because local-model
latency and browser navigation would reproduce the existing silent timeout experience.

**Use LangChain or LangGraph as the initial orchestration boundary.** Rejected for the
first version. The required state machine and tool policy are small, safety-critical,
and easier to audit directly. A framework may be adopted later if measured complexity
justifies it, without changing the domain tool contracts.

**Add MCP now.** Deferred at the owner's request. The domain tools can be adapted later
without making the first-party Chat tab depend on another transport or authentication
boundary.
