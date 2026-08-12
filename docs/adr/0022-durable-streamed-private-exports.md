# ADR-0022: Durable streamed private exports

**Status:** Accepted — 2026-08-12

## Context

A complete owner export can contain millions of exact Garmin samples and retained
source files. The original request handler loaded every database row into Python,
serialized one full JSON string, and kept the browser request open until completion.
The five-year benchmark in `docs/performance.md` demonstrated that this design was not
bounded by the size of one row or one chunk and could exhaust application memory.

Exports also contain high-sensitivity health data. A crashed worker must not expose a
partial artifact, credentials must remain excluded, and a browser refresh must not
lose the only status or download reference.

## Decision

Complete exports use the PostgreSQL queue from ADR-0004 and an owner-scoped
`ops.private_export` record.

1. `POST /api/v1/privacy/export` still requires the current password and CSRF token,
   plus an idempotency key. It returns `202` with a durable export and job reference.
2. The storage-isolated cleanup worker takes a repeatable-read snapshot, counts the
   selected rows, and emits compact JSON incrementally with server-side `yield_per`
   batches. It never constructs the longitudinal record as one object.
3. Progress is committed through separate short transactions. Owner-only list and
   detail routes show rows processed, total rows, attempts, next retry, safe error
   code, completion, expiry, and a download URL when ready.
4. The worker writes a mode-0600 temporary file, hashes it while streaming, fsyncs it,
   and atomically links it to its final private path. Only the final path is
   downloadable; retries converge on the first published checksum instead of
   overwriting it.
5. Facts, physician-approved plans, AI content, integrations, and reports remain
   explicitly separated. Every exact fact revision and provenance column is emitted.
   AI and sensitive text remain independent opt-ins. Credentials, authentication
   secrets, provider raw responses, and queue internals are explicitly omitted.
6. Stored lab source PDFs are embedded sequentially as base64 and integrity-checked
   against their recorded size and SHA-256. Report file bytes remain separate private
   downloads because their immutable metadata is already included.
7. Completed exports expire after seven days. A durable daily cleanup job removes
   artifact bytes and stale temporary files while retaining visible expired status.
   Account deletion refuses to race an active export and deletes completed export
   jobs, metadata, and the shared owner artifact directory.

## Consequences

- Export request latency and application memory no longer grow with record history.
- The browser can leave or refresh the page without losing progress or retry status.
- One export may run for minutes and uses more disk than the source JSON because exact
  retained binary inputs are base64 encoded. The expiration policy bounds that copy.
- A consistent snapshot may omit facts committed after generation began. Requesting a
  new export includes later facts; the artifact is never silently mutated.
- The cleanup worker receives a one-hour queue lease because this is the only
  benchmark-proven long-running owner operation. Atomic publication and idempotency
  remain the final defense if a lease is ever recovered by another worker.
