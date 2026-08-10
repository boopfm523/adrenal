# ADR-0012: Isolated read-only use of the unofficial Garmin Connect client

**Status:** Accepted — 2026-08-10

## Context

The Garmin feasibility spike recommended waiting for Garmin Developer Program access
and using reviewed FIT/CSV exports in the meantime. The owner subsequently made an
explicit product decision to use
[`cyberjunky/python-garminconnect`](https://github.com/cyberjunky/python-garminconnect)
for automatic personal-account ingestion.

That package is an unofficial client for Garmin Connect's private web services. It is
not Garmin's supported Health API, may stop working without notice, may trigger
additional authentication or account protections, and exposes mutating methods that
HealthCurve must never call. Login credentials and an MFA code are sent directly to
Garmin over HTTPS by the package. Refresh tokens are password-equivalent class-C8
secrets.

## Decision

HealthCurve may use a pinned, audited `python-garminconnect` release under these
boundaries:

1. A narrow adapter exposes only `get_stats`, `get_sleep_data`, and
   `get_activities_by_date`. No general Garmin client escapes the adapter and no
   mutating package method is called.
2. The integration is disabled by default. Garmin email and password come only from
   ignored environment variables. First login is a local CLI flow; MFA is read from a
   hidden prompt and is never persisted or logged.
3. The scheduled process uses a dedicated worker and token volume. It receives the
   database connection and Garmin-only environment variables, but no Telegram token,
   Ollama access, uploaded documents, report artifacts, or credential key ring. The
   token directory is outside the repository, mode 0700, and token files are mode
   0600. It is excluded from exports and HealthCurve backups.
4. Provider responses are untrusted. Bounded schema adapters select only the approved
   MVP fields and discard raw bodies after mapping. Raw responses, credentials, MFA
   values, and exception text never enter PostgreSQL, logs, API responses, reports,
   or Beads.
5. Sync windows, calls, retries, and backoff are bounded. Stable provider identities
   make overlapping syncs idempotent; a changed provider value creates a traceable
   correction rather than silently rewriting a recorded fact. Missing remains
   missing, never zero.
6. The initial metric contract is workouts; daily steps, resting heart rate, and
   average stress; and sleep start/end, duration, awakenings, and score when Garmin
   supplies them. Other fields remain deferred even if the client returns them.
7. Disconnect first disables future sync, then requires an owner-authenticated impact
   preview and exact confirmation before deleting provider-derived facts. Local token
   removal is automated; Garmin-side session revocation remains an owner action in
   Garmin account security because the unofficial client cannot guarantee revocation.
8. Reviewed owner-exported FIT/CSV/ZIP import remains supported as the durable fallback
   and is not replaced by this integration.

This decision supersedes only the direct-client prohibition and conditional no-go in
`docs/garmin-feasibility.md`; its findings about official API eligibility, missing
public contracts, attribution, privacy, and the export fallback remain valid.

## Consequences

Automatic personal sync becomes possible without a Garmin Developer Program account,
and its records remain in the same fact/provenance model as reviewed exports.

The integration carries more operational and account risk than an official API. It
can break when Garmin changes private endpoints or login, has no contractual schema or
quota, and cannot promise upstream token revocation. HealthCurve therefore reports
capability gaps and authentication failures explicitly, retains the manual import
fallback, and never treats a failed or missing sync as a health measurement.

## Alternatives considered

**Wait for official Garmin Health API approval.** Best supported boundary, but the
feasibility spike found that personal single-owner eligibility and terms could not be
assumed. Retained as a future migration path.

**Continue with exports only.** Safest and fully local, but requires repeated manual
work and does not meet the owner's explicit automatic-sync request. Retained as the
fallback.

**Run the general client in the API or existing Telegram worker.** Rejected because a
package or credential compromise would inherit unrelated secrets and a much broader
application role.
