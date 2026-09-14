# Analytics views for local-model analysis

HealthCurve's analytical chat and local MCP tools read a curated set of read-only views
instead of the application tables ([ADR-0036](adr/0036-local-model-analytical-chat.md)).
Only local Ollama models may use them; never connect them to a cloud model or
cloud-backed client.

## Access boundary

PostgreSQL enforces the boundary, not the model:

| Role | Login | Can read |
|---|---|---|
| `healthcurve_analytics_owner` | no | Owns the views; SELECT on the base tables they need. No `identity` access. |
| `healthcurve_analyst` | yes | `analytics` views only |
| `healthcurve_analyst_text` | yes | `analytics` and the opt-in free text in `analytics_text` |

Both analyst roles default to read-only transactions, a 15-second statement timeout,
and a 60-second idle-in-transaction timeout. Neither holds any privilege on `fact`,
`plan`, `ops`, `ai`, or `identity` tables, nor any write privilege. Integration tests
verify this against a database provisioned by the real init scripts.

## Enabling the roles

Migration `5e1a9c3d7b24` creates the schemas, views, grants, and the roles as NOLOGIN.
`deploy/postgres-init/03-analyst-roles.sh` enables login. On a new volume it runs
automatically when `POSTGRES_ANALYST_PASSWORD` and `POSTGRES_ANALYST_TEXT_PASSWORD` are
set. Without them it still creates the roles as NOLOGIN, so a restored backup that
grants to them loads cleanly.

For an existing volume:

1. Add strong, distinct values for `POSTGRES_ANALYST_PASSWORD` and
   `POSTGRES_ANALYST_TEXT_PASSWORD` to the ignored `.env`.
2. Apply migrations:

   ```bash
   docker compose run --rm api alembic upgrade head
   ```

3. Enable login with the idempotent script (rerunning it rotates the passwords):

   ```bash
   docker compose exec \
     -e POSTGRES_ANALYST_PASSWORD="$POSTGRES_ANALYST_PASSWORD" \
     -e POSTGRES_ANALYST_TEXT_PASSWORD="$POSTGRES_ANALYST_TEXT_PASSWORD" \
     postgres /docker-entrypoint-initdb.d/03-analyst-roles.sh
   ```

4. Set `HC_ANALYST_DATABASE_URL` and `HC_ANALYST_TEXT_DATABASE_URL` in `.env` to
   `postgresql+psycopg://<role>:<password>@postgres:5432/healthcurve`, then recreate the
   services that use them.

If either URL is unset, the corresponding analytical tools are unavailable. There is no
fallback to a broader role, and configuration rejects reusing the application or AI
connection.

## View semantics

Every view shows only the current revision of each record (corrections replace what they
supersede) and excludes voided doses. Instants are UTC (`*_at`); `local_*` columns are the
experienced wall-clock time in the row's IANA `timezone`.

| View | One row per | Notes |
|---|---|---|
| `analytics.sleep_sessions` | Garmin sleep session | `sleep_kind` is `overnight` or `nap`; `in_bed_minutes` is elapsed time, correct across DST |
| `analytics.sleep_nights` | local wake date | Longest current overnight session ending that date. `bedtime_minutes_after_noon` (minutes after 12:00 the previous day) and `wake_minutes_after_midnight` average correctly with plain `avg()` |
| `analytics.doses` | recorded dose | `minutes_after_wake` is signed minutes from that local date's wake; `minutes_from_scheduled` compares a fixed-time slot's clock time, wrapping across midnight |
| `analytics.daily_wearables` | local date | Garmin daily totals and summaries: steps, resting heart rate, average stress, nightly HRV, respiration averages |
| `analytics.daily_garmin_values` | Garmin daily summary value | Every provider daily field, with `source_field` |
| `analytics.daily_wearable_summaries` | local date × metric | Deterministic sample coverage, gaps, and min/avg/max |
| `analytics.wearable_samples` | timestamped Garmin sample | Heart rate, stress, respiration, HRV, hourly steps. Missing samples are absent, never zero |
| `analytics.activities` | Garmin activity | Sport, duration, distance, heart rate; no title or location |
| `analytics.symptoms` | symptom | Name, severity 0–10, body area, tracking category |
| `analytics.stress_episodes` | stress/up-dose episode | Status, severity, duration |
| `analytics.emergency_injections` | emergency injection | Medication, amount, route, escalation flags |
| `analytics.blood_pressure`, `analytics.temperatures`, `analytics.weights`, `analytics.meals`, `analytics.weather` | reading | Normalized units included; weather excludes location |
| `analytics.lab_results` | lab result | Original and normalized values, units, reference range, flag |
| `analytics.medications`, `analytics.plan_versions`, `analytics.plan_dose_slots` | plan record | Physician-approved plan data, separate from recorded facts; no clinician names or instruction text |
| `analytics_text.diary_entries`, `analytics_text.life_events`, `analytics_text.record_notes`, `analytics_text.plan_slot_conditions` | text item | Free text; readable only by the text role |

Modeled cortisol and exposure curves are computed in Python and are not in these views.

## Query tools

`healthcurve.analysis` exposes the views to local models through one versioned tool
catalog, shared by chat and the local MCP server:

- `describe_data` returns the view catalog (grain, category, columns), query conventions,
  and example queries; passing view names returns column types, units, and meanings.
  `healthcurve/analysis/catalog.py` is the single source of truth, and an integration
  test fails if it drifts from the migrated views.
- `run_query` accepts exactly one read-only `SELECT` (optionally `WITH`) over
  schema-qualified catalog views. A PostgreSQL-dialect parser rejects writes, DDL,
  `COPY`, `SET`, `SELECT INTO`, row locks, bind parameters, relations outside the
  catalog, text views without text access, and functions outside an allow-list. Clock
  functions such as `now()` and `current_date` are rejected so a stored query gives the
  same answer when re-run; models use literal dates.

Deterministic helpers cover calculations local models tend to get wrong. Their SQL is
fixed and parameterized, never model-authored:

- `clock_time_stats` reports the circular mean, median, earliest, latest, and spread of
  local clock times for bedtimes, wake times, all doses or the first dose of each day,
  meals, symptoms, or activity starts, so times either side of midnight stay adjacent.
- `event_window_stats` summarizes heart rate, stress, respiration, or HRV samples in a
  window before and after each symptom, dose, activity, meal, stress episode, wake, or
  bedtime. It returns per-event rows and an overall summary; missing samples are counted
  as absent, never zero.
- `modeled_exposure` returns values from the default modeled free-cortisol curve for a
  date, with the recorded-reference band position, labeled as modeled analysis. It
  needs domain services over base tables, so it runs only where the caller supplies a
  read-only application session; otherwise the tool is not offered.

Queries run on the analyst role in a read-only transaction with a 10-second statement
timeout, a server-side cursor, a 500-row cap, and a result-size cap; results report
truncation. Execution uses PostgreSQL's extended protocol, so the server rejects
multiple statements even if the parser were bypassed. Rejections and database errors
are returned to the model as short, repairable messages rather than failing the run.
