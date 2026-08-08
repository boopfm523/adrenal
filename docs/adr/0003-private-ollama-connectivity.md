# ADR-0003: Private Ollama connectivity

**Status:** Accepted — 2026-08-08

## Context

HealthCurve uses a local LLM (Ollama, Qwen) to turn natural-language capture into
schema-constrained extraction drafts. Two hard constraints apply:

- **Ollama must never be directly public** (plan §13). Ollama's HTTP API is
  unauthenticated by default; exposing it means anyone can use the model and,
  worse, probe whatever the application sends it.
- The model runs on hardware the owner controls. The production host may be a small
  VPS with no GPU, while the owner's home machine may have one. The plan names
  "production-to-local-Ollama topology" as a spike-worthy unknown (§16).

Model input is classified **C9** in the threat model — it contains health facts
verbatim — so the transport carrying it is as sensitive as the database connection.

## Decision

**Ollama is reachable only over a private network path, through a single adapter, with
the topology selected per environment by configuration.** Two supported topologies:

**Topology A — co-located (default).** Ollama runs as a Compose service on the
`hc-internal` network beside the api and worker. No `ports:` mapping. Reachable as
`http://ollama:11434` from inside the network only. This is the default for dev and
for production hosts with adequate CPU/GPU.

**Topology B — remote trusted machine.** Ollama runs on the owner's home machine and
is reached over a private tunnel (WireGuard or an equivalent authenticated overlay).
The production host holds one end of the tunnel; Ollama binds only to the tunnel
interface, never to a public one. The tunnel — not Ollama — provides authentication
and encryption.

Rules that hold under both topologies:

1. **All model access goes through one adapter module** (`ai.ollama`). No other module
   constructs an HTTP call to the model. Swapping topology is a configuration change,
   not a code change.
2. **The base URL is configuration**, validated at startup: it must resolve to a
   private address (RFC1918, loopback, or the tunnel's range). A public base URL is a
   startup failure, not a warning. This makes the "never public" rule enforced rather
   than remembered.
3. **The adapter is failure-first.** Explicit connect and read timeouts, bounded
   retries with jittered backoff, and a circuit breaker that trips to "model
   unavailable" rather than queueing unbounded work. Every failure path returns a
   typed unavailability result the caller must handle.
4. **Model unavailability is never fatal to the record.** Deterministic Telegram
   commands, the web forms, and the entire emergency page work with the model down
   (SAFE-21). Capture degrades to manual entry; nothing is lost and nothing is guessed.
5. **Strict output contract.** Requests specify a JSON Schema; responses are validated
   with Pydantic before anything else looks at them. Invalid JSON is a normal,
   test-covered outcome — never a partially-trusted record.
6. **Minimal input.** Only the message text, known medication names, and the current
   timezone are sent. No history, no credentials, no unrelated records — limiting what
   a prompt-injection attempt (T5) could exfiltrate.
7. **The model identity is recorded** — name and digest, plus prompt and schema
   version — on every draft and analysis (SAFE-05), so a model change is visible in
   the record and can gate regression evaluation.
8. **No model I/O bodies in logs** (C9, `redact: always`). Logs carry model name,
   prompt version, latency, and outcome only.

Selection between A and B, and the specific tunnel technology for B, is deferred to
the production phase; the adapter and the startup validation make the choice reversible.

## Consequences

Positive:

- Startup validation converts the most dangerous possible misconfiguration — a public
  Ollama — into a crash at boot rather than a silent exposure.
- One adapter means one place to test timeout, invalid-JSON, and circuit-breaker
  behavior, which the Phase 2 acceptance criteria require.
- The topology decision can be made late, when the actual production hardware is known.

Negative / costs:

- Topology B adds a tunnel to operate and monitor; its failure looks like model
  unavailability, which is handled but must be distinguishable in monitoring. Sync lag
  and circuit-breaker trips are alertable metrics.
- Keeping the model private means no managed inference and no failover — model
  downtime is real downtime for extraction. Accepted, because extraction is a
  convenience layer over a record that works without it.
- The private-address startup check will occasionally block a legitimate exotic setup.
  Accepted; overriding it requires an ADR superseding this one.

## Alternatives considered

**Hosted LLM API (Anthropic, OpenAI, or similar).** Better extraction quality and no
inference hardware to run. Rejected for this application: it would send verbatim
personal health text (C9) to a third party, contradicting the product's central
privacy premise. Reconsidering would require a new ADR and an explicit, informed
owner decision — not a quiet configuration change.

**Ollama published on the host with an authenticating proxy in front.** Adds an
authentication layer Ollama lacks, but keeps a public listener whose only protection
is that proxy's configuration. Rejected: a private network path has no listener to
misconfigure.

**Embedding the model in the application process** (llama.cpp bindings). Removes a
service and a network hop, but couples model lifecycle, memory, and crashes to the API
process — a model OOM would take down the record. Rejected; the record must stay up
when the model does not.
