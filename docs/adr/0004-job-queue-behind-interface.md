# ADR-0004: Database-backed job queue behind an interface

**Status:** Accepted — 2026-08-08

## Context

Background work in HealthCurve: Garmin sync and backfill, weather enrichment, LLM
extraction, PDF report rendering, exports, nightly backups, and integrity checks. The
plan permits "Dramatiq or RQ with Redis; a database-backed queue is acceptable
initially if kept behind an interface" (§5).

The workload is small and bursty — a single user's syncs and reports. What matters more
than throughput is that a job cannot half-apply: a Garmin import that writes some rows
and then fails must not leave the record in a state that looks complete.

## Decision

**A PostgreSQL-backed job queue, accessed only through a `operations.jobs` interface,
with Redis deferred until a need is demonstrated.**

1. **Interface first.** Modules enqueue via `jobs.enqueue(task, payload,
   idempotency_key=...)` and never touch the queue implementation. The interface
   defines: enqueue, claim, complete, fail, retry policy, dead-letter, and status
   query. Swapping to Dramatiq/RQ later replaces one adapter.
2. **Storage is a `operations.job` table** claimed with `SELECT ... FOR UPDATE SKIP
   LOCKED`, which gives correct concurrent claiming without a broker.
3. **Transactional enqueue.** Because the queue is in the same database, a job is
   enqueued in the same transaction as the rows that justify it. No lost jobs from a
   commit that succeeded while the broker call failed, and no jobs referencing rows
   that were rolled back — a genuine correctness advantage over an external broker.
4. **Every job is idempotent and carries an idempotency key.** Re-running a completed
   job is a no-op. This is required regardless of queue technology, because provider
   imports must be idempotent (plan §6).
5. **Bounded retries with exponential backoff, then dead-letter.** Dead-lettered jobs
   are visible on the data-quality/operations surface, never silently dropped. Queue
   age and dead-letter count are alertable metrics (plan §13).
6. **Jobs are visible.** Status, attempt count, last error (redacted per the data
   classification), and timing are queryable — the operator can answer "did last
   night's sync run?" from the database.
7. **Scheduled jobs** (nightly backup, integrity check, weather enrichment) use the
   same table with a due-time column, claimed by the same worker loop.

Redis remains in the Compose topology (ADR-0002) for rate limiting and caching; it is
**not** the queue.

## Consequences

Positive:

- One fewer stateful system in the durability path. The queue is backed up and
  restored with the database, so a restore does not lose in-flight work.
- Transactional enqueue removes an entire class of consistency bug.
- Job history is inspectable with ordinary SQL.

Negative / costs:

- Polling latency (a worker wakes on an interval rather than on a push). For jobs
  measured in seconds-to-minutes this is irrelevant; interactive LLM extraction runs
  in the request path with a timeout, not through the queue, precisely to avoid it.
- Throughput is bounded by database round-trips. Orders of magnitude above what one
  user generates.
- Writing a claim loop is code that Dramatiq/RQ would have provided. Mitigated by
  keeping it small (`SKIP LOCKED` does the hard part) and by testing claim contention,
  retry, and dead-letter paths explicitly.

## Alternatives considered

**Dramatiq or RQ with Redis now.** Mature, less code, push-based. Rejected initially
because it adds a second durability domain: a Redis loss loses queued work that the
database transaction thought was scheduled, and backup/restore must then cover two
systems. Revisit when a job workload appears that genuinely needs sub-second dispatch
or parallel throughput — the interface makes that a contained change.

**Celery.** Heavier configuration surface than the workload justifies. Rejected.

**In-process background tasks (FastAPI `BackgroundTasks`).** No durability at all — a
restart loses the work, with no retry and no visibility. Rejected for anything that
touches the record.

**External cron for scheduled work.** Splits scheduling across two systems and hides
failures outside the application's observability. Rejected; scheduled jobs use the
same table and the same visibility.
