from __future__ import annotations

import uuid
from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace
from typing import Any

from healthcurve.analytics.wearable_summaries import summarize_samples
from healthcurve.integrations.garmin.models import GarminMetricType


def sample(
    *,
    occurred_at: datetime,
    value: str = "72",
    unit: str = "bpm",
    cadence: int | None = 300,
    revision: str = "v1",
    identifier: uuid.UUID | None = None,
) -> Any:
    return SimpleNamespace(
        id=identifier or uuid.uuid4(),
        occurred_at=occurred_at,
        recorded_at=occurred_at.astimezone(UTC),
        source_revision=revision,
        supersedes_id=None,
        metric_type=GarminMetricType.HEART_RATE,
        value=Decimal(value),
        unit=unit,
        sample_interval_seconds=cadence,
    )


def test_summary_uses_actual_spring_dst_day_and_reports_gaps() -> None:
    owner_id = uuid.uuid4()
    row = sample(occurred_at=datetime(2026, 3, 8, 7, 0, tzinfo=UTC))

    result = summarize_samples(
        owner_id=owner_id,
        day=date(2026, 3, 8),
        timezone="America/New_York",
        metric=GarminMetricType.HEART_RATE,
        samples=[row],
    )

    # Five observed minutes divided by the real 23-hour local day.
    assert result["observed_coverage_minutes"] == Decimal("5.0000")
    assert result["observed_coverage_percent"] == Decimal("0.3623")
    assert result["gap_count"] == 2
    assert result["largest_gap_minutes"] == Decimal("1255.0000")


def test_summary_preserves_missing_and_unknown_cadence_instead_of_zero() -> None:
    owner_id = uuid.uuid4()
    missing = summarize_samples(
        owner_id=owner_id,
        day=date(2026, 11, 1),
        timezone="America/New_York",
        metric=GarminMetricType.HRV,
        samples=[],
    )
    assert missing["sample_count"] == 0
    assert missing["minimum"] is None
    assert missing["average"] is None
    assert missing["maximum"] is None
    assert missing["gap_count"] is None
    assert missing["largest_gap_minutes"] is None
    assert missing["missingness_state"] == "no_samples"

    cadence_unknown = summarize_samples(
        owner_id=owner_id,
        day=date(2026, 11, 1),
        timezone="America/New_York",
        metric=GarminMetricType.HEART_RATE,
        samples=[sample(occurred_at=datetime(2026, 11, 1, 6, 0, tzinfo=UTC), cadence=None)],
    )
    assert cadence_unknown["sample_count"] == 1
    assert cadence_unknown["samples_without_cadence"] == 1
    assert cadence_unknown["average"] == Decimal("72.0000")
    assert cadence_unknown["gap_count"] is None
    assert cadence_unknown["missingness_state"] == "cadence_unavailable"


def test_summary_uses_actual_fall_dst_day() -> None:
    result = summarize_samples(
        owner_id=uuid.uuid4(),
        day=date(2026, 11, 1),
        timezone="America/New_York",
        metric=GarminMetricType.HEART_RATE,
        samples=[sample(occurred_at=datetime(2026, 11, 1, 6, 0, tzinfo=UTC))],
    )

    # Five observed minutes divided by the real 25-hour local day.
    assert result["observed_coverage_percent"] == Decimal("0.3333")


def test_summary_watermark_and_values_change_with_revision() -> None:
    owner_id = uuid.uuid4()
    occurred_at = datetime(2026, 8, 12, 12, 0, tzinfo=UTC)
    identifier = uuid.uuid4()
    original = summarize_samples(
        owner_id=owner_id,
        day=date(2026, 8, 12),
        timezone="UTC",
        metric=GarminMetricType.HEART_RATE,
        samples=[
            sample(
                occurred_at=occurred_at,
                value="70",
                revision="v1",
                identifier=identifier,
            )
        ],
    )
    revised = summarize_samples(
        owner_id=owner_id,
        day=date(2026, 8, 12),
        timezone="UTC",
        metric=GarminMetricType.HEART_RATE,
        samples=[
            sample(
                occurred_at=occurred_at,
                value="80",
                revision="v2",
                identifier=identifier,
            )
        ],
    )

    assert original["average"] == Decimal("70.0000")
    assert revised["average"] == Decimal("80.0000")
    assert (
        original["source_revision_watermark_sha256"] != revised["source_revision_watermark_sha256"]
    )


def test_incompatible_units_are_not_combined() -> None:
    owner_id = uuid.uuid4()
    result = summarize_samples(
        owner_id=owner_id,
        day=date(2026, 8, 12),
        timezone="UTC",
        metric=GarminMetricType.HEART_RATE,
        samples=[
            sample(occurred_at=datetime(2026, 8, 12, 12, 0, tzinfo=UTC), unit="bpm"),
            sample(occurred_at=datetime(2026, 8, 12, 12, 5, tzinfo=UTC), unit="other"),
        ],
    )
    assert result["sample_count"] == 2
    assert result["incompatible_units"] is True
    assert result["unit"] is None
    assert result["average"] is None
