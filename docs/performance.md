# Performance budgets

HealthCurve is a private, single-owner application. Its common interactive views must
stay responsive after several years of use without assuming that old health facts can
be discarded.

The PostgreSQL integration gate uses a rolled-back, all-synthetic six-year fixture:

- four recorded dose facts per day (8,760 rows);
- a symptom every three days and a diary entry every seven days;
- six historical physician-plan versions with four daily slots each; and
- the real migrated PostgreSQL schema and ORM loading behavior.

After one warm-up request, the median of seven runs must meet these service budgets:

| View | Budget | Measured work |
| --- | ---: | --- |
| Timeline | 750 ms | Merge and serialize the latest 200 items across every timeline fact type |
| Today | 250 ms | Resolve the historical plan, load one local day of doses, and compare slots |
| Plan comparison | 100 ms | Load two versions and produce their deterministic slot diff |
| Daily pattern features | 2,000 ms | Derive and serialize 366 local days of current-fact exposure, context ranges, coverage, and revision fingerprints |

These deliberately include ORM loading and deterministic transformation, but exclude
browser/network latency. The test also requires PostgreSQL to choose an index-backed
plan for the high-volume owner/time Timeline lookup. A wall-clock pass without an
index check is insufficient because a small or unusually fast CI machine could hide a
future full-table scan.

Run the gate with:

```bash
uv run pytest tests/integration/test_api_safety.py::test_common_views_meet_latency_targets_on_six_year_synthetic_volume
```

The targets are regression budgets, not production telemetry or medical guarantees.
Synthetic benchmark rows are created inside a transaction and rolled back.

## Dense wearable scale benchmark

The common-view CI gate above intentionally contains ordinary human-entered facts; it
does not model Garmin's native intraday cadence. The separate wearable benchmark uses
an empty, disposable, fully migrated PostgreSQL database and a rollback-only synthetic
owner. Its default five-year fixture contains:

- heart rate every 2 minutes for the full day (720 samples/day);
- stress every 3 minutes for the full day (480 samples/day);
- respiration every 2 minutes for the full day (720 samples/day);
- HRV every 5 minutes in an 8-hour nightly observation window (96 samples/day); and
- one distinct daily aggregate for each metric (4 aggregate facts/day).

Starting on 2020-01-01, five years includes 1,827 days, 3,683,232 provider samples,
7,308 aggregate facts, and 3,690,540 total metric rows. These cadences come from the
observed contracts in ADR-0014; they are workload assumptions, not promises about what
Garmin will supply.

The runner refuses any database that already contains an owner. It measures warmed
application latency for the latest Timeline page, selected-day HealthCurve, 31-day
analytics, and a seven-day wearable report snapshot with HTML/CSV/JSON assembly. It
also captures `EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` for selected-day, 366-day,
Timeline, and complete-export metric reads; relation size for backup planning; and a
10,000-row JSON serialization sample. The current complete export and 366-day ORM
transform are deliberately not executed in full because doing so would reproduce the
unbounded-memory defect the benchmark is intended to expose. The result labels that
omission rather than presenting an extrapolation as a measurement.

Run it only against a disposable database created for this purpose:

```bash
uv run python scripts/benchmark_wearable_scale.py \
  --database-url postgresql+psycopg://USER@HOST/EMPTY_MIGRATED_BENCHMARK_DB \
  --years 5 --runs 3 \
  --confirm ROLLBACK-SYNTHETIC-WEARABLE-BENCHMARK \
  --output /tmp/healthcurve-wearable-benchmark.json
```

The output is versioned JSON. `measurements` are complete warmed application timings;
`query_plans` are database-side measurements and retain their node trees;
`storage.garmin_metric_relation_bytes` is the contribution that logical backups must
read; and `findings` names current paths that cannot safely be timed end-to-end at full
scale. Results contain synthetic counts and operational timings only—no health values,
credentials, account identifiers, or owner data.

### Five-year baseline — 2026-08-12

The first full run used PostgreSQL 16 in the project's Docker Desktop stack on the
owner's Apple Silicon workstation, three warmed application runs, and the exact
3,690,540-row fixture above. The transaction rollback was verified afterward: both
`identity.owner` and `fact.garmin_metric_event` contained zero benchmark rows.

| Path | Measured median / database execution | Evidence |
| --- | ---: | --- |
| Latest Timeline page (25 rows) | 730.872 ms | Complete warmed application operation |
| Selected-day HealthCurve | 62.414 ms | Complete warmed application operation |
| 31-day analytics | 1,959.783 ms | Complete warmed application operation |
| Seven-day wearable report snapshot + HTML/CSV/JSON | 914.161 ms | Complete warmed application operation; excludes Chromium/PDF startup |
| Selected-day provider samples | 0.512 ms | PostgreSQL `Index Scan` |
| 366-day provider samples | 668.698 ms | PostgreSQL `Seq Scan` |
| Timeline daily aggregates, latest 200 | 17.912 ms | PostgreSQL `Limit` → `Index Scan` |
| Complete-export Garmin metric scan | 790.410 ms | PostgreSQL `Seq Scan`; excludes ORM materialization and JSON response construction |

The migrated `fact.garmin_metric_event` relation, including indexes, occupied
2,378,407,936 bytes (2,268.227 MiB). Querying and JSON-encoding a bounded 10,000-row
export sample produced 2,333,961 bytes in 38.137 ms. Those export figures are a bounded
probe, not a claim that the existing complete export can safely materialize all rows.

This baseline establishes the work for the remaining `hc-jgd` children:

- preserve the fast selected-day index path while fixing longitudinal and
  current-revision query plans;
- replace raw multi-month sample materialization with deterministic daily summaries;
- queue and stream complete exports rather than assembling them in request memory;
- use the measured 2.27-GiB five-year relation in the retention, backup, and isolated
  restore decision.

### Index and current-revision optimization — 2026-08-12

`hc-jgd.2` repeated the same five-year, 3,690,540-row fixture after adding a
partial owner/time index for non-provider-sample Garmin aggregates and moving
current-revision exclusion into the initial SQL reads. The transaction rollback was
again verified: both `identity.owner` and `fact.garmin_metric_event` contained zero
benchmark rows afterward.

| Path | Baseline | Optimized | Evidence |
| --- | ---: | ---: | --- |
| Latest Timeline page (25 rows) | 730.872 ms | 5.774 ms | Complete warmed application operation; about 126× faster |
| Selected-day HealthCurve | 62.414 ms | 47.078 ms | Complete warmed application operation |
| 31-day analytics | 1,959.783 ms | 1,385.491 ms | Complete warmed application operation |
| Seven-day wearable report snapshot + HTML/CSV/JSON | 914.161 ms | 842.184 ms | Complete warmed application operation |
| Timeline daily aggregates, latest 200 | 17.912 ms | 0.056 ms | PostgreSQL `Limit` → `Index Only Scan` using `ix_garmin_metric_owner_aggregate_occurred` |
| Timeline aggregate count | not captured | 1.080 ms | PostgreSQL `Aggregate` → `Index Only Scan` over 7,308 aggregate rows |

The selected-day provider-sample path remains index-backed. PostgreSQL rationally
retains sequential scans for a 366-day raw-sample result and a complete export because
those operations return a material fraction of the 3.69-million-row table. Versioned
daily summaries now address the longitudinal shape; queued, streamed exports remain
separate work instead of forcing inappropriate indexes.

## Versioned daily wearable summaries

Longitudinal analytics and report snapshots now read
`ops.wearable_daily_summary` instead of materializing dense raw samples. A cold cache
is filled in at most 31-local-day raw chunks; warm reads return at most four rows per
day. Exact selected-day HealthCurve samples continue to come from recorded facts.

Operators can prefill a date range after migration or during a quiet window. The
command is safe to interrupt and rerun: every chunk commits independently and uses an
idempotent version-keyed upsert.

```bash
uv run python scripts/backfill_wearable_daily_summaries.py \
  --date-from 2020-01-01 --date-to 2026-08-12 --chunk-days 31
```

Use `--database-url` or `HC_DATABASE_URL` to select the database. Output contains only
owner/chunk counts and the summary version—never health values or owner identifiers.
Late provider samples and immutable corrections invalidate affected cached days in
PostgreSQL; the next analytics/report read rebuilds them deterministically.

### Summary scale verification — 2026-08-12

`hc-jgd.3` repeated the five-year, 3,690,540-row fixture and executed the complete
366-day application projection. The first uncached request built 1,464 daily summary
rows from 737,856 exact raw observations in bounded 31-day chunks. The synthetic
transaction rollback was verified afterward: `identity.owner`,
`fact.garmin_metric_event`, and `ops.wearable_daily_summary` each contained zero rows.

| Path | Median | Evidence |
| --- | ---: | --- |
| 366-day analytics, cold summary materialization | 14,605.478 ms | One complete application operation; each raw query bounded to at most 31 local days |
| 366-day analytics, warm summaries | 256.776 ms | Three complete application operations over 1,464 summary rows |
| Selected-day HealthCurve | 27.932 ms | Three complete application operations retaining 2,016 exact samples |
| 31-day analytics | 22.415 ms | Three complete application operations using warm summaries |
| Seven-day report snapshot + HTML/CSV/JSON | 9.005 ms | Three complete report operations using summaries plus sparse exact Garmin records |
| 366-day summary-table database scan | 0.292 ms | PostgreSQL scan of 1,464 rows; no dense raw materialization |
| Comparable 366-day raw database scan | 657.763 ms | PostgreSQL scan of 737,856 rows, retained only as a diagnostic comparison |

The cold result is intentionally reported rather than hidden: on-demand rebuilding a
full uncached year is bounded in memory but still user-visible work. Run the restartable
backfill after migration, or before known long-range review, so ordinary requests use
the warm path. Late or corrected provider observations invalidate only their affected
local dates; the following bounded read deterministically refreshes those dates.
