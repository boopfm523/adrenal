# ADR-0001: PostgreSQL as the datastore

**Status:** Accepted — 2026-08-08

## Context

HealthCurve's value is a trustworthy longitudinal record. The data model
(`docs/HealthCurve_Project_Plan.md` §6) demands things a datastore must actually
enforce rather than merely allow:

- **Exact decimals for medication amounts.** Binary floating point is prohibited —
  15.0 mg must never become 14.999999. This needs a true `NUMERIC` type.
- **Non-overlapping regimen version intervals**, enforced by the database, not by
  application code that a future bug can bypass.
- **Namespace separation** of facts, plans, and AI output (SAFE-01), enforceable at
  the schema and role level.
- **Timezone-correct temporal data**: UTC instants plus original local time, IANA zone,
  and offset, with correct DST arithmetic.
- **Provider-idempotent upserts** keyed on `(provider, provider_id, revision)`.
- Room to grow into time-series queries over wearable samples.

The deployment is a single-owner, self-hosted service. Operational simplicity matters,
but not at the cost of the invariants above.

## Decision

**PostgreSQL 16 or newer is the datastore for all environments that hold real data.**

Specifically:

1. Medication amounts use `NUMERIC(10,4)` with a separate non-null unit column.
   Floating-point columns are prohibited for any clinical quantity.
2. Regimen version non-overlap is enforced with an exclusion constraint over a
   `daterange`/`tstzrange` (`btree_gist`), not by application logic alone.
3. Facts, plans, and AI output live in separate PostgreSQL **schemas** (`fact`,
   `plan`, `ai`), which gives SAFE-01 a structural home and makes SAFE-15/SAFE-16
   enforceable with per-schema `GRANT`s: the AI worker's role has no `INSERT`/`UPDATE`
   on `fact` or `plan`.
4. Timestamps use `timestamptz` for the canonical instant, alongside explicit columns
   for original local time, IANA timezone name, and UTC offset. The application never
   relies on the server's local timezone.
5. Provider idempotency uses unique constraints plus `INSERT ... ON CONFLICT`.
6. Extensions assumed available: `btree_gist` (exclusion constraints), `pgcrypto`
   (digests for source checksums). Both ship with the official image.
7. Schema changes go through Alembic migrations, reviewed and applied deliberately —
   never auto-generated and applied in the same step.

**SQLite is permitted only for disposable prototypes and for unit tests that touch no
schema-level invariant.** Any test asserting an exclusion constraint, a schema grant,
a `NUMERIC` behavior, or an `ON CONFLICT` path runs against real PostgreSQL via
Testcontainers. A test suite that passes only on SQLite is not evidence.

## Consequences

Positive:

- The invariants that protect clinical correctness are enforced by the database, so
  they survive application bugs and direct-SQL fixes.
- Per-schema grants turn "AI cannot write facts" from a code convention into a
  privilege boundary — the strongest available form of SAFE-15/SAFE-16.
- Mature backup/restore story (`pg_dump` logical backups) matching plan §13.

Negative / costs:

- A database server must run in every environment that holds real data, including the
  developer's; Docker Compose absorbs this.
- Tests that need real constraint behavior are slower than in-memory SQLite. Accepted:
  the suite is split so fast pure-logic tests stay in-memory and constraint tests run
  against a container.
- Two database roles (application, AI worker) must be provisioned and kept in sync
  with migrations. Migrations must explicitly grant on new tables; a test asserts the
  AI role's lack of write access so a forgotten grant fails CI rather than silently
  widening AI's reach.

## Alternatives considered

**SQLite everywhere.** Simplest to operate and genuinely adequate for a single-user
record's volume. Rejected: no schema-level privilege separation (so SAFE-15/16 would
be code-only), weaker exclusion-constraint support for the non-overlap rule, and
`NUMERIC` affinity that does not give true fixed-point arithmetic. The safety
boundaries are the product; giving up their strongest enforcement to save one
container is the wrong trade.

**MySQL/MariaDB.** Adequate decimals, but no exclusion constraints (non-overlap would
return to application logic) and weaker range/interval types.

**A document store (MongoDB).** The model is highly relational with hard invariants
across entities. Rejected on the same grounds — the invariants would all move into
application code.

**PostgreSQL + TimescaleDB for wearable samples.** Attractive later for biometric
sample volume. Deferred: adds an extension dependency to the backup and restore path
before the volume justifies it. Revisit if wearable sample queries become slow; plain
PostgreSQL partitioning is the intermediate step.
