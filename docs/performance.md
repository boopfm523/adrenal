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
