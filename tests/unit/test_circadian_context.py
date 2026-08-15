from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from itertools import pairwise
from typing import Any, cast

import pytest

from healthcurve.analytics import circadian_context


@pytest.mark.parametrize(
    ("hour", "center", "lower", "upper"),
    [
        (0.0, "2.200000000", "1.760000000", "2.640000000"),
        (7.5, "22.000000000", "17.600000000", "26.400000000"),
        (20.0, "4.000000000", "3.200000000", "4.800000000"),
        (24.0, "2.200000000", "1.760000000", "2.640000000"),
    ],
)
def test_band_matches_adr_0024_anchor_fixtures(
    hour: float, center: str, lower: str, upper: str
) -> None:
    assert circadian_context.band_values_at_local_hour(hour) == (
        Decimal(center),
        Decimal(lower),
        Decimal(upper),
    )


def test_pchip_is_shape_preserving_between_every_anchor() -> None:
    anchors = circadian_context.ANCHORS
    x = tuple(anchor[0] for anchor in anchors)
    y = tuple(anchor[1] for anchor in anchors)
    for index in range(len(anchors) - 1):
        lower = min(y[index], y[index + 1])
        upper = max(y[index], y[index + 1])
        for step in range(101):
            hour = x[index] + (x[index + 1] - x[index]) * step / 100
            value = circadian_context.pchip_value(x, y, hour)
            assert lower <= value <= upper


def test_default_band_samples_every_five_elapsed_minutes() -> None:
    band = cast(
        dict[str, Any],
        circadian_context.build_band(day=date(2026, 8, 11), timezone="America/New_York"),
    )
    samples = band["samples"]

    assert len(samples) == 289
    assert samples[0]["occurred_at"] == datetime(2026, 8, 11, 4, tzinfo=UTC)
    assert samples[-1]["occurred_at"] == datetime(2026, 8, 12, 4, tzinfo=UTC)
    assert all(
        right["occurred_at"] - left["occurred_at"] == timedelta(minutes=5)
        for left, right in pairwise(samples)
    )
    assert all(sample["lower_nmol_l"] <= sample["center_nmol_l"] for sample in samples)
    assert all(sample["center_nmol_l"] <= sample["upper_nmol_l"] for sample in samples)


@pytest.mark.parametrize(
    ("day", "elapsed_hours", "sample_count"),
    [
        (date(2026, 3, 8), Decimal("23.0"), 277),
        (date(2026, 11, 1), Decimal("25.0"), 301),
    ],
)
def test_band_uses_real_elapsed_dst_day(
    day: date, elapsed_hours: Decimal, sample_count: int
) -> None:
    band = cast(
        dict[str, Any],
        circadian_context.build_band(day=day, timezone="America/New_York"),
    )
    instants = [sample["occurred_at"] for sample in band["samples"]]

    assert band["elapsed_hours"] == elapsed_hours
    assert len(instants) == sample_count
    assert instants == sorted(set(instants))


def test_repeated_fall_back_clock_time_has_distinct_offsets_and_same_band_value() -> None:
    band = cast(
        dict[str, Any],
        circadian_context.build_band(day=date(2026, 11, 1), timezone="America/New_York"),
    )
    repeated = [
        sample
        for sample in band["samples"]
        if sample["local_time"].hour == 1 and sample["local_time"].minute == 30
    ]

    assert [
        (sample["local_time"].isoformat(), sample["utc_offset_minutes"]) for sample in repeated
    ] == [
        ("2026-11-01T01:30:00", -240),
        ("2026-11-01T01:30:00", -300),
    ]
    assert repeated[0]["center_nmol_l"] == repeated[1]["center_nmol_l"]


def test_custom_model_instants_align_band_without_inventing_samples() -> None:
    instants = [
        datetime(2026, 8, 11, 4, tzinfo=UTC),
        datetime(2026, 8, 11, 11, 30, tzinfo=UTC),
        datetime(2026, 8, 12, 4, tzinfo=UTC),
    ]
    band = cast(
        dict[str, Any],
        circadian_context.build_band(
            day=date(2026, 8, 11),
            timezone="America/New_York",
            sample_instants=reversed(instants),
        ),
    )

    assert [sample["occurred_at"] for sample in band["samples"]] == instants
    assert band["samples"][1]["center_nmol_l"] == Decimal("22.000000000")


def test_stress_context_is_recorded_but_neutral_and_never_personalizes_band() -> None:
    band = cast(
        dict[str, Any],
        circadian_context.build_band(
            day=date(2026, 8, 11),
            timezone="UTC",
            recorded_episode_count=3,
            missing_episode_severity_count=2,
        ),
    )
    context = band["recorded_stress_context"]
    metadata = band["band"]

    assert context == {
        "episode_count": 3,
        "missing_severity_count": 2,
        "multiplier": Decimal("1.0"),
        "applied_to_band": False,
        "applied_to_drug_model": False,
        "reason": "No validated individual stress-to-cortisol-demand mapping in v2.0.0.",
    }
    assert metadata["personalized"] is False
    assert metadata["body_context_used"] is False
    assert metadata["demographic_reference_interval"] is False
    assert band["default_visible"] is False
    assert "normal range" in band["safety_label"]
    assert "medication requirement" in band["safety_label"]


def test_invalid_episode_context_and_sample_windows_fail_closed() -> None:
    with pytest.raises(ValueError, match="nonnegative"):
        circadian_context.build_band(
            day=date(2026, 8, 11), timezone="UTC", recorded_episode_count=-1
        )
    with pytest.raises(ValueError, match="cannot exceed"):
        circadian_context.build_band(
            day=date(2026, 8, 11),
            timezone="UTC",
            recorded_episode_count=1,
            missing_episode_severity_count=2,
        )
    with pytest.raises(ValueError, match="within"):
        circadian_context.build_band(
            day=date(2026, 8, 11),
            timezone="UTC",
            sample_instants=[datetime(2026, 8, 10, 23, 59, tzinfo=UTC)],
        )
