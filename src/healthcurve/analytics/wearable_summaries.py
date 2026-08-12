"""Bounded, deterministic daily summaries of dense wearable facts.

The rows produced here are a rebuildable operational projection. Garmin metric
events remain the authoritative facts and missing observations are never replaced
with zeroes.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from healthcurve.events import service as event_service
from healthcurve.integrations.garmin.models import (
    GarminMetricEvent,
    GarminMetricType,
    WearableDailySummary,
)

SUMMARY_VERSION: Final = "hc-wearable-daily-v1"
MAX_RAW_CHUNK_DAYS: Final = 31
DISPLAY_QUANTUM: Final = Decimal("0.0001")
METRICS: Final = (
    GarminMetricType.STRESS,
    GarminMetricType.HEART_RATE,
    GarminMetricType.HRV,
    GarminMetricType.RESPIRATION_RATE,
)


def _day_bounds(day: date, timezone: str) -> tuple[datetime, datetime]:
    zone = ZoneInfo(timezone)
    return (
        datetime.combine(day, time.min, tzinfo=zone).astimezone(UTC),
        datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC),
    )


def _watermark(tokens: Iterable[str]) -> str:
    body = json.dumps(sorted(tokens), ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _sample_token(row: GarminMetricEvent) -> str:
    return "\x1f".join(
        (
            str(row.id),
            row.occurred_at.astimezone(UTC).isoformat(),
            row.recorded_at.astimezone(UTC).isoformat(),
            row.source_revision or "",
            str(row.supersedes_id or ""),
            row.metric_type.value,
            str(row.value),
            row.unit,
            str(row.sample_interval_seconds or ""),
        )
    )


def summarize_samples(
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
    metric: GarminMetricType,
    samples: Sequence[GarminMetricEvent],
) -> dict[str, object]:
    """Return one summary, including honest coverage and gap semantics."""
    start, end = _day_bounds(day, timezone)
    units = sorted({row.unit for row in samples})
    compatible = len(units) <= 1
    values = [row.value for row in samples] if compatible else []
    intervals: list[tuple[datetime, datetime]] = []
    missing_cadence = 0
    for row in samples:
        if row.sample_interval_seconds is None:
            missing_cadence += 1
            continue
        interval_start = max(start, row.occurred_at.astimezone(UTC))
        interval_end = min(
            end,
            row.occurred_at.astimezone(UTC) + timedelta(seconds=row.sample_interval_seconds),
        )
        if interval_end > interval_start:
            intervals.append((interval_start, interval_end))
    intervals.sort()
    merged: list[list[datetime]] = []
    for interval_start, interval_end in intervals:
        if not merged or interval_start > merged[-1][1]:
            merged.append([interval_start, interval_end])
        else:
            merged[-1][1] = max(merged[-1][1], interval_end)

    observed_seconds = sum(
        (
            Decimal(str((interval_end - interval_start).total_seconds()))
            for interval_start, interval_end in merged
        ),
        Decimal(0),
    )
    day_seconds = Decimal(str((end - start).total_seconds()))
    coverage_minutes = (observed_seconds / Decimal(60)).quantize(DISPLAY_QUANTUM)
    coverage_percent = (observed_seconds * Decimal(100) / day_seconds).quantize(DISPLAY_QUANTUM)

    gap_count: int | None = None
    largest_gap_minutes: Decimal | None = None
    if intervals:
        gaps: list[Decimal] = []
        cursor = start
        for interval_start, interval_end in merged:
            if interval_start > cursor:
                gaps.append(Decimal(str((interval_start - cursor).total_seconds())))
            cursor = max(cursor, interval_end)
        if cursor < end:
            gaps.append(Decimal(str((end - cursor).total_seconds())))
        gap_count = len(gaps)
        largest_gap_minutes = (max(gaps, default=Decimal(0)) / Decimal(60)).quantize(
            DISPLAY_QUANTUM
        )

    if not samples:
        state = "no_samples"
    elif not intervals:
        state = "cadence_unavailable"
    elif observed_seconds >= day_seconds:
        state = "full_observed_coverage"
    else:
        state = "partial_observed_coverage"

    tokens = [
        f"summary:{SUMMARY_VERSION}",
        f"owner:{owner_id}",
        f"date:{day.isoformat()}",
        f"timezone:{timezone}",
        f"metric:{metric.value}",
        *(_sample_token(row) for row in samples),
    ]
    average = (
        (sum(values, Decimal(0)) / Decimal(len(values))).quantize(DISPLAY_QUANTUM)
        if values
        else None
    )
    return {
        "owner_id": owner_id,
        "local_date": day,
        "timezone": timezone,
        "metric_type": metric,
        "unit": units[0] if len(units) == 1 else None,
        "sample_count": len(samples),
        "samples_without_cadence": missing_cadence,
        "observed_coverage_minutes": coverage_minutes,
        "observed_coverage_percent": coverage_percent,
        "gap_count": gap_count,
        "largest_gap_minutes": largest_gap_minutes,
        "missingness_state": state,
        "incompatible_units": not compatible,
        "minimum": min(values) if values else None,
        "average": average,
        "maximum": max(values) if values else None,
        "source_revision_watermark_sha256": _watermark(tokens),
        "summary_version": SUMMARY_VERSION,
        "refreshed_at": datetime.now(UTC),
    }


def _chunks(date_from: date, date_to: date) -> Iterable[tuple[date, date]]:
    cursor = date_from
    while cursor <= date_to:
        chunk_end = min(date_to, cursor + timedelta(days=MAX_RAW_CHUNK_DAYS - 1))
        yield cursor, chunk_end
        cursor = chunk_end + timedelta(days=1)


def _summary_key(row: WearableDailySummary) -> tuple[date, GarminMetricType]:
    return row.local_date, row.metric_type


def ensure_daily_summaries(
    session: Session,
    *,
    owner_id: uuid.UUID,
    date_from: date,
    date_to: date,
    timezone: str,
) -> list[WearableDailySummary]:
    """Return current summaries, rebuilding only absent rows in bounded chunks."""
    existing = list(
        session.scalars(
            select(WearableDailySummary).where(
                WearableDailySummary.owner_id == owner_id,
                WearableDailySummary.local_date >= date_from,
                WearableDailySummary.local_date <= date_to,
                WearableDailySummary.timezone == timezone,
                WearableDailySummary.summary_version == SUMMARY_VERSION,
                WearableDailySummary.metric_type.in_(METRICS),
            )
        )
    )
    available = {_summary_key(row) for row in existing}

    for chunk_start, chunk_end in _chunks(date_from, date_to):
        missing = {
            (day, metric)
            for offset in range((chunk_end - chunk_start).days + 1)
            for day in (chunk_start + timedelta(days=offset),)
            for metric in METRICS
            if (day, metric) not in available
        }
        if not missing:
            continue
        start, _ = _day_bounds(chunk_start, timezone)
        _, end = _day_bounds(chunk_end, timezone)
        samples = list(
            session.scalars(
                select(GarminMetricEvent)
                .where(
                    GarminMetricEvent.owner_id == owner_id,
                    GarminMetricEvent.aggregation == "provider_sample",
                    GarminMetricEvent.metric_type.in_(METRICS),
                    GarminMetricEvent.occurred_at >= start,
                    GarminMetricEvent.occurred_at < end,
                    event_service.current_fact_predicate(GarminMetricEvent, owner_id=owner_id),
                )
                .order_by(GarminMetricEvent.occurred_at, GarminMetricEvent.id)
            )
        )
        by_key: dict[tuple[date, GarminMetricType], list[GarminMetricEvent]] = {}
        zone = ZoneInfo(timezone)
        for sample in samples:
            key = (sample.occurred_at.astimezone(zone).date(), sample.metric_type)
            if key in missing:
                by_key.setdefault(key, []).append(sample)
        values = [
            summarize_samples(
                owner_id=owner_id,
                day=day,
                timezone=timezone,
                metric=metric,
                samples=by_key.get((day, metric), []),
            )
            for day, metric in sorted(missing, key=lambda item: (item[0], item[1].value))
        ]
        statement = insert(WearableDailySummary).values(values)
        statement = statement.on_conflict_do_update(
            constraint="uq_wearable_daily_summary_identity",
            set_={
                column: getattr(statement.excluded, column)
                for column in (
                    "unit",
                    "sample_count",
                    "samples_without_cadence",
                    "observed_coverage_minutes",
                    "observed_coverage_percent",
                    "gap_count",
                    "largest_gap_minutes",
                    "missingness_state",
                    "incompatible_units",
                    "minimum",
                    "average",
                    "maximum",
                    "source_revision_watermark_sha256",
                    "refreshed_at",
                )
            },
        )
        session.execute(statement)
        available.update(missing)

    return list(
        session.scalars(
            select(WearableDailySummary)
            .where(
                WearableDailySummary.owner_id == owner_id,
                WearableDailySummary.local_date >= date_from,
                WearableDailySummary.local_date <= date_to,
                WearableDailySummary.timezone == timezone,
                WearableDailySummary.summary_version == SUMMARY_VERSION,
                WearableDailySummary.metric_type.in_(METRICS),
            )
            .order_by(WearableDailySummary.local_date, WearableDailySummary.metric_type)
        )
    )


def as_feature(row: WearableDailySummary) -> dict[str, object]:
    return {
        "metric_type": row.metric_type,
        "unit": row.unit,
        "sample_count": row.sample_count,
        "samples_without_cadence": row.samples_without_cadence,
        "observed_coverage_minutes": row.observed_coverage_minutes,
        "observed_coverage_percent": row.observed_coverage_percent,
        "gap_count": row.gap_count,
        "largest_gap_minutes": row.largest_gap_minutes,
        "missingness_state": row.missingness_state,
        "incompatible_units": row.incompatible_units,
        "minimum": row.minimum,
        "average": row.average,
        "maximum": row.maximum,
        "source_revision_watermark_sha256": row.source_revision_watermark_sha256,
        "summary_version": row.summary_version,
    }
