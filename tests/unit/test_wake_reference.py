from __future__ import annotations

import csv
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from healthcurve.analytics.wake_reference import (
    REFERENCE_ID,
    build_reference,
    free_from_total,
    total_from_free,
)

FIXTURE = Path(__file__).parents[1] / "fixtures" / "analytics" / "cortisol-reference-example.csv"
FOUR_PLACES = Decimal("0.0001")


def _reference(
    *,
    day: date = date(2026, 8, 11),
    wake_hour: int = 6,
    wake_minute: int = 15,
    sleep_hour: int = 23,
    meals: bool = True,
) -> dict[str, Any]:
    zone = ZoneInfo("America/New_York")
    observed_meals = None
    if meals:
        observed_meals = {
            "breakfast": datetime.combine(day, datetime.min.time(), zone).replace(hour=7),
            "lunch": datetime.combine(day, datetime.min.time(), zone).replace(hour=12, minute=30),
            "dinner": datetime.combine(day, datetime.min.time(), zone).replace(hour=18, minute=45),
        }
    return cast(
        dict[str, Any],
        build_reference(
            day=day,
            timezone="America/New_York",
            wake_at=datetime.combine(day, datetime.min.time(), zone).replace(
                hour=wake_hour, minute=wake_minute
            ),
            sleep_onset_at=datetime.combine(day, datetime.min.time(), zone).replace(
                hour=sleep_hour
            ),
            meals=observed_meals,
            age_years=47,
            sex="M",
        ),
    )


def _sample_at(result: dict[str, Any], hour: float) -> dict[str, Any]:
    return min(result["samples"], key=lambda sample: abs(float(sample["hour_local"]) - hour))


def test_supplied_reference_fixture_matches_to_four_decimal_places() -> None:
    result = _reference()
    with FIXTURE.open(newline="") as fixture:
        expected = list(csv.DictReader(fixture))

    samples = result["samples"]
    assert len(samples) == len(expected) == 289
    field_pairs = (
        ("serum_total_p5_nmol_l", "serum_total_p5"),
        ("serum_total_p50_nmol_l", "serum_total_p50"),
        ("serum_total_p95_nmol_l", "serum_total_p95"),
        ("serum_free_p5_nmol_l", "serum_free_p5"),
        ("serum_free_p50_nmol_l", "serum_free_p50"),
        ("serum_free_p95_nmol_l", "serum_free_p95"),
    )
    for sample, row in zip(samples, expected, strict=True):
        assert sample["hour_local"].quantize(FOUR_PLACES) == Decimal(row["hour_local"])
        assert sample["hours_since_wake"].quantize(FOUR_PLACES) == Decimal(row["hours_since_wake"])
        for actual_field, expected_field in field_pairs:
            assert sample[actual_field].quantize(FOUR_PLACES) == Decimal(row[expected_field])


def test_all_five_percentiles_are_ordered_on_free_and_total_scales() -> None:
    result = _reference()

    assert result["reference"]["percentiles"] == ["p5", "p25", "p50", "p75", "p95"]
    for sample in result["samples"]:
        for scale in ("free", "total"):
            values = [
                sample[f"serum_{scale}_{percentile}_nmol_l"]
                for percentile in ("p5", "p25", "p50", "p75", "p95")
            ]
            assert values == sorted(values)


@pytest.mark.parametrize("free", [0.5, 1.0, 10.0, 38.0, 100.0, 200.0])
def test_binding_conversion_round_trips(free: float) -> None:
    assert free_from_total(total_from_free(free)) == pytest.approx(free, abs=1e-6)


def test_reference_acceptance_shape_and_binding_checks() -> None:
    result = _reference()
    samples = result["samples"]
    peak = max(samples, key=lambda sample: sample["serum_free_p50_nmol_l"])
    free_values = [float(sample["serum_free_p50_nmol_l"]) for sample in samples]
    total_values = [float(sample["serum_total_p50_nmol_l"]) for sample in samples]

    assert float(peak["hour_local"]) == pytest.approx(6 + 50 / 60, abs=1 / 120)
    assert float(peak["serum_total_p50_nmol_l"]) == pytest.approx(499, abs=5)
    assert float(_sample_at(result, 12.25)["serum_total_p50_nmol_l"]) == pytest.approx(176, abs=8)
    assert float(_sample_at(result, 18.25)["serum_total_p50_nmol_l"]) == pytest.approx(77, abs=8)
    assert min(total_values) == pytest.approx(45, abs=4)
    assert max(free_values) / min(free_values) == pytest.approx(20, abs=2)
    assert max(total_values) / min(total_values) == pytest.approx(11, abs=1)


def test_later_wake_changes_morning_but_converges_by_evening() -> None:
    early = _reference(wake_hour=6, wake_minute=15)
    late = _reference(wake_hour=8, wake_minute=30)

    morning_ratio = float(_sample_at(late, 7)["serum_free_p50_nmol_l"]) / float(
        _sample_at(early, 7)["serum_free_p50_nmol_l"]
    )
    evening_ratio = float(_sample_at(late, 21)["serum_free_p50_nmol_l"]) / float(
        _sample_at(early, 21)["serum_free_p50_nmol_l"]
    )
    assert morning_ratio == pytest.approx(0.41, abs=0.04)
    assert evening_ratio == pytest.approx(1.0, abs=0.06)


def test_unobserved_meals_are_not_invented() -> None:
    without_meals = _reference(meals=False)
    with_meals = _reference(meals=True)

    assert without_meals["assumptions"]["observed_meals"] == {}
    assert without_meals["assumptions"]["unobserved_meals_invented"] is False
    assert float(_sample_at(with_meals, 13.5)["serum_free_p50_nmol_l"]) > float(
        _sample_at(without_meals, 13.5)["serum_free_p50_nmol_l"]
    )


@pytest.mark.parametrize(
    ("wake_present", "sleep_present", "missing"),
    [
        (False, True, ["wake_at"]),
        (True, False, ["sleep_onset_at"]),
        (False, False, ["wake_at", "sleep_onset_at"]),
    ],
)
def test_missing_sleep_timing_stays_missing(
    wake_present: bool, sleep_present: bool, missing: list[str]
) -> None:
    day = date(2026, 8, 11)
    zone = ZoneInfo("America/New_York")
    result = cast(
        dict[str, Any],
        build_reference(
            day=day,
            timezone="America/New_York",
            wake_at=datetime(2026, 8, 11, 6, 15, tzinfo=zone) if wake_present else None,
            sleep_onset_at=(datetime(2026, 8, 10, 23, tzinfo=zone) if sleep_present else None),
        ),
    )

    assert result["available"] is False
    assert result["missing_inputs"] == missing
    assert result["samples"] == []
    assert result["reference"]["id"] == REFERENCE_ID


@pytest.mark.parametrize(
    ("day", "expected_samples", "expected_hours"),
    [(date(2026, 3, 8), 277, Decimal("23.0")), (date(2026, 11, 1), 301, Decimal("25.0"))],
)
def test_reference_grid_follows_real_dst_day_duration(
    day: date, expected_samples: int, expected_hours: Decimal
) -> None:
    result = _reference(day=day, meals=False)

    assert len(result["samples"]) == expected_samples
    assert result["elapsed_hours"] == expected_hours


def test_repeated_fall_back_clock_values_keep_distinct_offsets() -> None:
    result = _reference(day=date(2026, 11, 1), meals=False)
    repeated = [
        sample
        for sample in result["samples"]
        if sample["local_time"].hour == 1 and sample["local_time"].minute == 30
    ]

    assert len(repeated) == 2
    assert {sample["utc_offset_minutes"] for sample in repeated} == {-300, -240}
    assert repeated[0]["serum_free_p50_nmol_l"] == repeated[1]["serum_free_p50_nmol_l"]


def test_invalid_inputs_fail_instead_of_silently_changing_assumptions() -> None:
    day = date(2026, 8, 11)
    zone = ZoneInfo("America/New_York")
    with pytest.raises(ValueError, match="unsupported meal role"):
        build_reference(
            day=day,
            timezone="America/New_York",
            wake_at=datetime(2026, 8, 11, 6, 15, tzinfo=zone),
            sleep_onset_at=datetime(2026, 8, 10, 23, tzinfo=zone),
            meals={"snack": datetime(2026, 8, 11, 15, tzinfo=zone)},
        )
    with pytest.raises(ValueError, match="sex must"):
        build_reference(
            day=day,
            timezone="America/New_York",
            wake_at=datetime(2026, 8, 11, 6, 15, tzinfo=zone),
            sleep_onset_at=datetime(2026, 8, 10, 23, tzinfo=zone),
            sex="unspecified",
        )
