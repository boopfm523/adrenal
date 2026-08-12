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
