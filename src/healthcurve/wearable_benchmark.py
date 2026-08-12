"""Synthetic, rollback-only benchmark for long-term Garmin storage paths.

The full benchmark deliberately runs only against an empty, migrated PostgreSQL
database.  It never reads an owner's record and rolls its synthetic owner and facts
back when complete.  The pure scale helpers are kept here so CI can verify the
fixture math without inserting millions of rows.
"""

from __future__ import annotations

import json
import uuid
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, timedelta
from statistics import median
from time import perf_counter
from typing import Final

from sqlalchemy import Connection, Engine, text
from sqlalchemy.orm import Session

from healthcurve.analytics import day_analysis, patterns
from healthcurve.api.pagination import PageRequest
from healthcurve.api.routers import events as events_router
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.models import (
    GarminSyncOrigin,
    GarminSyncRun,
    GarminSyncStatus,
)
from healthcurve.reports import builder as report_builder
from healthcurve.reports import rendering as report_rendering

RESULT_SCHEMA_VERSION: Final = 2
MIN_YEARS: Final = 2
MAX_YEARS: Final = 10


@dataclass(frozen=True, slots=True)
class MetricScale:
    metric_type: str
    cadence_minutes: int
    observed_minutes_per_day: int

    @property
    def samples_per_day(self) -> int:
        return self.observed_minutes_per_day // self.cadence_minutes


METRIC_SCALES: Final = (
    MetricScale("heart_rate", cadence_minutes=2, observed_minutes_per_day=1_440),
    MetricScale("stress", cadence_minutes=3, observed_minutes_per_day=1_440),
    MetricScale("respiration_rate", cadence_minutes=2, observed_minutes_per_day=1_440),
    MetricScale("hrv", cadence_minutes=5, observed_minutes_per_day=480),
)


@dataclass(frozen=True, slots=True)
class ScalePlan:
    years: int
    start_date: date
    end_date_exclusive: date
    days: int
    provider_samples_per_day: int
    daily_aggregates_per_day: int
    provider_sample_rows: int
    daily_aggregate_rows: int
    total_metric_rows: int
    metrics: tuple[MetricScale, ...]


class BenchmarkSafetyError(RuntimeError):
    """The caller attempted to use the destructive-load benchmark unsafely."""


def build_scale_plan(*, years: int, start_date: date = date(2020, 1, 1)) -> ScalePlan:
    """Return exact fixture volume, including leap days, without touching a database."""
    if not MIN_YEARS <= years <= MAX_YEARS:
        raise ValueError(f"years must be between {MIN_YEARS} and {MAX_YEARS}")
    try:
        end = start_date.replace(year=start_date.year + years)
    except ValueError:
        # A February 29 start has no matching date in most later years.
        end = start_date.replace(month=2, day=28, year=start_date.year + years)
    days = (end - start_date).days
    provider_per_day = sum(metric.samples_per_day for metric in METRIC_SCALES)
    aggregate_per_day = len(METRIC_SCALES)
    provider_rows = days * provider_per_day
    aggregate_rows = days * aggregate_per_day
    return ScalePlan(
        years=years,
        start_date=start_date,
        end_date_exclusive=end,
        days=days,
        provider_samples_per_day=provider_per_day,
        daily_aggregates_per_day=aggregate_per_day,
        provider_sample_rows=provider_rows,
        daily_aggregate_rows=aggregate_rows,
        total_metric_rows=provider_rows + aggregate_rows,
        metrics=METRIC_SCALES,
    )


def result_skeleton(scale: ScalePlan) -> dict[str, object]:
    """Stable machine-readable result shape shared by the runner and CI tests."""
    scale_value = asdict(scale)
    scale_value["start_date"] = scale.start_date.isoformat()
    scale_value["end_date_exclusive"] = scale.end_date_exclusive.isoformat()
    return {
        "schema_version": RESULT_SCHEMA_VERSION,
        "fixture": "synthetic_rollback_only",
        "scale": scale_value,
        "actual_rows": {},
        "measurements": [],
        "query_plans": {},
        "storage": {},
        "findings": [],
    }


def require_empty_database(connection: Connection) -> None:
    owners = connection.scalar(text("SELECT count(*) FROM identity.owner"))
    if owners:
        raise BenchmarkSafetyError(
            "wearable benchmark requires an empty, disposable migrated database; "
            "refusing to share a database with any owner record"
        )


def _seed_metric_rows(
    connection: Connection, *, owner_id: uuid.UUID, sync_run_id: uuid.UUID, scale: ScalePlan
) -> None:
    metric_specs = json.dumps(
        [
            {
                "metric_type": item.metric_type,
                "cadence_minutes": item.cadence_minutes,
                "observed_minutes": item.observed_minutes_per_day,
                "unit": _unit(item.metric_type),
                "field_name": _field_name(item.metric_type),
            }
            for item in scale.metrics
        ]
    )
    connection.execute(
        text(
            """
            INSERT INTO fact.garmin_metric_event (
                id, owner_id, occurred_at, local_time, timezone, utc_offset_minutes,
                recorded_at, source_type, provider_id, source_revision,
                confirmation_state, garmin_sync_run_id, garmin_source_member,
                garmin_manufacturer, metric_type, value, unit, aggregation,
                sample_interval_seconds, garmin_field_name
            )
            SELECT
                gen_random_uuid(), :owner_id,
                day_start + minute_offset * interval '1 minute',
                (day_start + minute_offset * interval '1 minute') AT TIME ZONE 'UTC',
                'UTC', 0,
                day_start + minute_offset * interval '1 minute' + interval '1 minute',
                'provider',
                format('benchmark:%s:%s', metric_type,
                    extract(epoch FROM day_start + minute_offset * interval '1 minute')),
                md5(format('%s:%s', metric_type,
                    extract(epoch FROM day_start + minute_offset * interval '1 minute'))),
                'provider_imported', :sync_run_id, 'synthetic-benchmark',
                'Synthetic Garmin', metric_type,
                CASE metric_type
                    WHEN 'heart_rate' THEN 55 + (minute_offset % 75)
                    WHEN 'stress' THEN minute_offset % 101
                    WHEN 'respiration_rate' THEN 10 + (minute_offset % 15)
                    ELSE 25 + (minute_offset % 55)
                END,
                unit, 'provider_sample', cadence_minutes * 60, field_name
            FROM generate_series(
                CAST(:start_at AS timestamptz),
                CAST(:end_at AS timestamptz) - interval '1 day',
                interval '1 day'
            ) AS day(day_start)
            CROSS JOIN jsonb_to_recordset(CAST(:metric_specs AS jsonb)) AS metric(
                metric_type text, cadence_minutes integer, observed_minutes integer,
                unit text, field_name text
            )
            CROSS JOIN LATERAL generate_series(
                0, observed_minutes - cadence_minutes, cadence_minutes
            ) AS sample(minute_offset)
            """
        ),
        {
            "owner_id": owner_id,
            "sync_run_id": sync_run_id,
            "start_at": datetime.combine(scale.start_date, datetime.min.time(), tzinfo=UTC),
            "end_at": datetime.combine(scale.end_date_exclusive, datetime.min.time(), tzinfo=UTC),
            "metric_specs": metric_specs,
        },
    )
    connection.execute(
        text(
            """
            INSERT INTO fact.garmin_metric_event (
                id, owner_id, occurred_at, local_time, timezone, utc_offset_minutes,
                recorded_at, source_type, provider_id, source_revision,
                confirmation_state, garmin_sync_run_id, garmin_source_member,
                garmin_manufacturer, metric_type, value, unit, aggregation,
                sample_interval_seconds, garmin_field_name
            )
            SELECT
                gen_random_uuid(), :owner_id, day_start,
                day_start AT TIME ZONE 'UTC', 'UTC', 0, day_start + interval '1 day',
                'provider', format('benchmark:daily:%s:%s', metric_type,
                    extract(epoch FROM day_start)),
                md5(format('daily:%s:%s', metric_type, extract(epoch FROM day_start))),
                'provider_imported', :sync_run_id, 'synthetic-benchmark',
                'Synthetic Garmin', metric_type,
                CASE metric_type
                    WHEN 'heart_rate' THEN 62
                    WHEN 'stress' THEN 30
                    WHEN 'respiration_rate' THEN 15
                    ELSE 42
                END,
                unit, 'daily_summary', NULL, field_name
            FROM generate_series(
                CAST(:start_at AS timestamptz),
                CAST(:end_at AS timestamptz) - interval '1 day',
                interval '1 day'
            ) AS day(day_start)
            CROSS JOIN jsonb_to_recordset(CAST(:metric_specs AS jsonb)) AS metric(
                metric_type text, cadence_minutes integer, observed_minutes integer,
                unit text, field_name text
            )
            """
        ),
        {
            "owner_id": owner_id,
            "sync_run_id": sync_run_id,
            "start_at": datetime.combine(scale.start_date, datetime.min.time(), tzinfo=UTC),
            "end_at": datetime.combine(scale.end_date_exclusive, datetime.min.time(), tzinfo=UTC),
            "metric_specs": metric_specs,
        },
    )


def _unit(metric_type: str) -> str:
    return {
        "heart_rate": "bpm",
        "stress": "garmin_score",
        "respiration_rate": "breaths/min",
        "hrv": "ms",
    }[metric_type]


def _field_name(metric_type: str) -> str:
    return {
        "heart_rate": "heartRateValues",
        "stress": "stressValuesArray",
        "respiration_rate": "respirationValuesArray",
        "hrv": "hrvReadings",
    }[metric_type]


def _measure(name: str, operation: Callable[[], object], *, runs: int) -> dict[str, object]:
    operation()
    samples = []
    for _ in range(runs):
        started = perf_counter()
        operation()
        samples.append((perf_counter() - started) * 1_000)
    return {
        "name": name,
        "runs": runs,
        "median_ms": round(median(samples), 3),
        "minimum_ms": round(min(samples), 3),
        "maximum_ms": round(max(samples), 3),
    }


def _measure_once(name: str, operation: Callable[[], object]) -> dict[str, object]:
    started = perf_counter()
    operation()
    elapsed = round((perf_counter() - started) * 1_000, 3)
    return {
        "name": name,
        "runs": 1,
        "median_ms": elapsed,
        "minimum_ms": elapsed,
        "maximum_ms": elapsed,
    }


def _explain(
    connection: Connection, statement: str, parameters: Mapping[str, object]
) -> dict[str, object]:
    payload = connection.execute(
        text(f"EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) {statement}"), parameters
    ).scalar_one()[0]
    nodes: list[str] = []

    def visit(node: dict[str, object]) -> None:
        nodes.append(str(node.get("Node Type")))
        for child in node.get("Plans", []):  # type: ignore[union-attr]
            visit(child)

    visit(payload["Plan"])
    return {
        "planning_ms": round(float(payload.get("Planning Time", 0)), 3),
        "execution_ms": round(float(payload.get("Execution Time", 0)), 3),
        "node_types": nodes,
        "plan": payload["Plan"],
    }


def _serialized_sample(connection: Connection, owner_id: uuid.UUID) -> dict[str, object]:
    started = perf_counter()
    rows = connection.execute(
        text(
            "SELECT id, occurred_at, metric_type, value, unit, aggregation, provider_id "
            "FROM fact.garmin_metric_event WHERE owner_id = :owner_id LIMIT 10000"
        ),
        {"owner_id": owner_id},
    ).mappings()
    encoded = json.dumps([dict(row) for row in rows], default=str, separators=(",", ":"))
    elapsed_ms = (perf_counter() - started) * 1_000
    return {
        "sample_rows": 10_000,
        "sample_bytes": len(encoded.encode("utf-8")),
        "sample_query_and_json_ms": round(elapsed_ms, 3),
    }


def run_benchmark(engine: Engine, *, years: int = 5, runs: int = 3) -> dict[str, object]:
    """Run the measured benchmark in one transaction and always roll it back."""
    if not 1 <= runs <= 9:
        raise ValueError("runs must be between 1 and 9")
    scale = build_scale_plan(years=years)
    result = result_skeleton(scale)
    with engine.connect() as connection:
        transaction = connection.begin()
        session = Session(bind=connection, expire_on_commit=False)
        try:
            require_empty_database(connection)
            owner = Owner(
                email=f"wearable-benchmark-{uuid.uuid4()}@example.test",
                password_hash="synthetic-non-login-hash",  # noqa: S106  # pragma: allowlist secret
                default_timezone="UTC",
            )
            session.add(owner)
            session.flush()
            sync = GarminSyncRun(
                owner_id=owner.id,
                requested_start_date=scale.start_date,
                requested_end_date=scale.end_date_exclusive - timedelta(days=1),
                timezone="UTC",
                origin=GarminSyncOrigin.MANUAL,
                status=GarminSyncStatus.COMPLETED,
                started_at=datetime.now(UTC),
                finished_at=datetime.now(UTC),
                counts={},
                warning_codes=[],
                client_version="synthetic-benchmark-v1",
            )
            session.add(sync)
            session.flush()
            _seed_metric_rows(connection, owner_id=owner.id, sync_run_id=sync.id, scale=scale)
            connection.execute(text("ANALYZE fact.garmin_metric_event"))

            provider_count = connection.scalar(
                text(
                    "SELECT count(*) FROM fact.garmin_metric_event "
                    "WHERE owner_id=:owner_id AND aggregation='provider_sample'"
                ),
                {"owner_id": owner.id},
            )
            aggregate_count = connection.scalar(
                text(
                    "SELECT count(*) FROM fact.garmin_metric_event "
                    "WHERE owner_id=:owner_id AND aggregation<>'provider_sample'"
                ),
                {"owner_id": owner.id},
            )
            result["actual_rows"] = {
                "provider_samples": provider_count,
                "daily_aggregates": aggregate_count,
                "garmin_metrics": (provider_count or 0) + (aggregate_count or 0),
            }
            if (
                provider_count != scale.provider_sample_rows
                or aggregate_count != scale.daily_aggregate_rows
            ):
                raise AssertionError("database fixture counts do not match scale plan")

            selected_day = scale.end_date_exclusive - timedelta(days=1)
            month_start = selected_day - timedelta(days=30)
            year_start_date = selected_day - timedelta(days=365)

            def timeline() -> object:
                session.expire_all()
                return events_router.timeline(
                    session=session,
                    owner=owner,
                    pagination=PageRequest(page=1, page_size=25),
                    date_from=None,
                    date_to=None,
                    types=None,
                    timezone="UTC",
                    local_date_from=None,
                    local_date_to=None,
                    include_sensitive=False,
                    sort_order="desc",
                )

            def daily_healthcurve() -> object:
                session.expire_all()
                return day_analysis.build_projection(
                    session, owner_id=owner.id, day=selected_day, timezone="UTC"
                )

            def monthly_analytics() -> object:
                session.expire_all()
                return patterns.daily_patterns_for_owner(
                    session,
                    owner_id=owner.id,
                    date_from=month_start,
                    date_to=selected_day,
                    timezone="UTC",
                )

            def annual_analytics() -> object:
                session.expire_all()
                return patterns.daily_patterns_for_owner(
                    session,
                    owner_id=owner.id,
                    date_from=year_start_date,
                    date_to=selected_day,
                    timezone="UTC",
                )

            def seven_day_report_snapshot() -> object:
                session.expire_all()
                snapshot = report_builder.build_snapshot(
                    session,
                    owner_id=owner.id,
                    date_from=selected_day - timedelta(days=6),
                    date_to=selected_day,
                    timezone="UTC",
                    selected_sections=["metrics", "wearables"],
                )
                # Exercise deterministic report assembly, but not Chromium/PDF startup.
                report_rendering.render_html(snapshot)
                report_rendering.render_csv(snapshot)
                report_rendering.render_json(snapshot)
                session.expunge(snapshot)
                return None

            result["measurements"] = [
                _measure_once(
                    "longitudinal_analytics_366_days_cold_materialization", annual_analytics
                ),
                _measure("timeline_latest_25", timeline, runs=runs),
                _measure("selected_day_healthcurve", daily_healthcurve, runs=runs),
                _measure("monthly_analytics_31_days", monthly_analytics, runs=runs),
                _measure("longitudinal_analytics_366_days", annual_analytics, runs=runs),
                _measure(
                    "seven_day_report_snapshot_html_csv_json", seven_day_report_snapshot, runs=runs
                ),
            ]

            day_start = datetime.combine(selected_day, datetime.min.time(), tzinfo=UTC)
            year_start = datetime.combine(year_start_date, datetime.min.time(), tzinfo=UTC)
            connection.execute(text("ANALYZE ops.wearable_daily_summary"))
            result["actual_rows"]["wearable_daily_summaries"] = connection.scalar(
                text("SELECT count(*) FROM ops.wearable_daily_summary WHERE owner_id=:owner_id"),
                {"owner_id": owner.id},
            )
            parameters = {
                "owner_id": owner.id,
                "day_start": day_start,
                "day_end": day_start + timedelta(days=1),
                "year_start": year_start,
                "year_end": day_start + timedelta(days=1),
            }
            result["query_plans"] = {
                "selected_day_samples": _explain(
                    connection,
                    "SELECT * FROM fact.garmin_metric_event WHERE owner_id=:owner_id "
                    "AND aggregation='provider_sample' AND occurred_at>=:day_start "
                    "AND occurred_at<:day_end",
                    parameters,
                ),
                "longitudinal_366_day_samples": _explain(
                    connection,
                    "SELECT * FROM fact.garmin_metric_event WHERE owner_id=:owner_id "
                    "AND aggregation='provider_sample' AND occurred_at>=:year_start "
                    "AND occurred_at<:year_end",
                    parameters,
                ),
                "longitudinal_366_day_summaries": _explain(
                    connection,
                    "SELECT * FROM ops.wearable_daily_summary WHERE owner_id=:owner_id "
                    "AND local_date>=CAST(:year_start AS date) "
                    "AND local_date<CAST(:year_end AS date) "
                    "AND timezone='UTC' AND summary_version='hc-wearable-daily-v1'",
                    parameters,
                ),
                "timeline_daily_aggregates": _explain(
                    connection,
                    "SELECT id, occurred_at FROM fact.garmin_metric_event "
                    "WHERE owner_id=:owner_id AND aggregation<>'provider_sample' "
                    "ORDER BY occurred_at DESC LIMIT 200",
                    parameters,
                ),
                "timeline_daily_aggregate_count": _explain(
                    connection,
                    "SELECT count(*) FROM fact.garmin_metric_event "
                    "WHERE owner_id=:owner_id AND aggregation<>'provider_sample'",
                    parameters,
                ),
                "complete_export_metric_scan": _explain(
                    connection,
                    "SELECT * FROM fact.garmin_metric_event WHERE owner_id=:owner_id",
                    parameters,
                ),
            }
            relation_bytes = connection.scalar(
                text("SELECT pg_total_relation_size('fact.garmin_metric_event')")
            )
            result["storage"] = {
                "garmin_metric_relation_bytes": relation_bytes,
                "garmin_metric_relation_mib": round(float(relation_bytes or 0) / 1_048_576, 3),
                "export_serialization_sample": _serialized_sample(connection, owner.id),
                "backup_scope": (
                    "Relation size is the dense-wearable contribution that pg_dump and "
                    "encrypted backup storage must process; end-to-end backup/restore is "
                    "measured by the retention decision child issue."
                ),
            }
            result["findings"] = [
                (
                    "The complete export path is synchronous and unbounded. The benchmark "
                    "measures its full database scan and a 10,000-row JSON sample instead of "
                    "constructing a multi-million-row response in application memory."
                ),
                (
                    "Selected-day HealthCurve retains exact provider samples. Longitudinal "
                    "analytics and reports consume versioned daily summaries, and cold-cache "
                    "raw reads are bounded to at most 31 local days."
                ),
                (
                    "The benchmark executes the complete 366-day application projection and "
                    "captures both the raw database plan for comparison and the bounded "
                    "summary-table plan used after deterministic materialization."
                ),
            ]
            result["generated_at"] = datetime.now(UTC).isoformat()
            return result
        finally:
            session.close()
            transaction.rollback()


def result_json(result: dict[str, object]) -> str:
    return json.dumps(result, indent=2, sort_keys=True, default=str) + "\n"
