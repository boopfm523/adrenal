"""Theoretical healthy cortisol response to Garmin exercise load (ADR-0037). Synthetic data."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, cast
from zoneinfo import ZoneInfo

import pytest

from healthcurve.analytics import exercise_response as er
from healthcurve.analytics.wake_reference import build_reference

DAY = date(2026, 3, 10)
ZONE = "America/New_York"
TZ = ZoneInfo(ZONE)
REST = 60.0
MAX_ESTIMATE = er.age_estimated_max_heart_rate(47.0)


def _local(hour: int, minute: int = 0, day: date = DAY) -> datetime:
    return datetime.combine(day, time(hour, minute), tzinfo=TZ)


REFERENCE = build_reference(
    day=DAY,
    timezone=ZONE,
    wake_at=_local(6, 30),
    sleep_onset_at=_local(22, 45, DAY - timedelta(days=1)),
)


def _bpm(intensity: float) -> float:
    return REST + intensity * (MAX_ESTIMATE - REST)


def _samples(
    start: datetime, minutes: int, intensity: float, cadence: int = 60
) -> list[er.HeartRateSample]:
    return [
        er.HeartRateSample(
            at=start + timedelta(seconds=cadence * index),
            bpm=_bpm(intensity),
            interval_seconds=cadence,
        )
        for index in range(minutes * 60 // cadence)
    ]


def _build(samples: list[er.HeartRateSample], **overrides: Any) -> dict[str, Any]:
    options: dict[str, Any] = {
        "day": DAY,
        "timezone": ZONE,
        "reference": REFERENCE,
        "heart_rate": samples,
        "resting_heart_rate_bpm": REST,
        "resting_heart_rate_source": "garmin_daily",
        "observed_peak_heart_rate_bpm": None,
        **overrides,
    }
    return er.build_response(**options)


def _sample_at(response: dict[str, Any], instant: datetime) -> dict[str, Any]:
    target = instant.astimezone(UTC)
    return next(sample for sample in response["samples"] if sample["occurred_at"] == target)


def _increment_at(response: dict[str, Any], instant: datetime) -> float:
    return float(_sample_at(response, instant)["increment_fraction"])


@pytest.mark.parametrize(
    ("intensity", "expected"),
    [(0.30, 0.0), (0.40, 0.0), (0.60, 0.415), (0.80, 0.83)],
)
def test_thirty_minutes_reproduces_the_published_intensity_threshold(
    intensity: float, expected: float
) -> None:
    response = _build(_samples(_local(10), 30, intensity))
    assert response["available"] is True
    assert _increment_at(response, _local(10, 30)) == pytest.approx(expected, abs=0.002)


def test_the_healthy_response_decays_with_the_stated_half_life() -> None:
    response = _build(_samples(_local(10), 30, 0.80))
    peak = _increment_at(response, _local(10, 30))
    assert _increment_at(response, _local(11, 45)) == pytest.approx(peak / 2, abs=0.005)


def test_long_hard_efforts_stay_within_the_threefold_bound() -> None:
    response = _build(_samples(_local(8), 300, 1.0))
    increments = [float(sample["increment_fraction"]) for sample in response["samples"]]
    assert max(increments) == pytest.approx(2.0)
    assert float(response["summary"]["peak_increment_fraction"]) == pytest.approx(2.0)


def test_unobserved_heart_rate_contributes_no_load() -> None:
    sparse = [
        er.HeartRateSample(at=_local(10), bpm=_bpm(0.9), interval_seconds=60),
        er.HeartRateSample(at=_local(12), bpm=_bpm(0.9), interval_seconds=60),
    ]
    response = _build(sparse)
    assert response["inputs"]["heart_rate_observed_minutes"] == 2
    assert response["inputs"]["heart_rate_unobserved_minutes"] > 1_000
    assert float(response["summary"]["peak_increment_fraction"]) < 0.1


def test_the_reference_band_is_raised_only_where_load_occurred() -> None:
    response = _build(_samples(_local(10), 30, 0.80))
    base = {
        sample["occurred_at"]: sample for sample in cast(list[dict[str, Any]], REFERENCE["samples"])
    }
    loaded = _sample_at(response, _local(10, 30))
    quiet = _sample_at(response, _local(9))
    for label in ("p5", "p50", "p95"):
        key = f"serum_free_{label}_nmol_l"
        assert loaded[key] > base[loaded["occurred_at"]][key]
        assert quiet[key] == base[quiet["occurred_at"]][key]
    assert quiet["increment_fraction"] == Decimal("0.0000")
    assert float(response["summary"]["extra_median_free_nmol_l_hours"]) > 0


def test_resting_heart_rate_all_day_raises_nothing() -> None:
    response = _build(_samples(_local(0), 24 * 60, 0.0))
    assert response["summary"]["minutes_above_threshold"] == 0
    assert response["summary"]["extra_median_free_nmol_l_hours"] == Decimal("0E-9")


def test_activity_rows_summarize_load_and_recovery() -> None:
    activity = er.ActivityWindow(
        activity_id="synthetic-run",
        sport="running",
        started_at=_local(10),
        ended_at=_local(10, 30),
    )
    response = _build(_samples(_local(10), 30, 0.80), activities=[activity])
    (row,) = response["activities"]
    assert row["minutes_above_threshold"] == 30
    assert row["observed_heart_rate_minutes"] == 30
    assert float(row["mean_intensity_hrr"]) == pytest.approx(0.80, abs=0.001)
    assert float(row["peak_increment_fraction"]) == pytest.approx(0.83, abs=0.002)


def test_observed_peak_above_the_age_estimate_sets_maximum_heart_rate() -> None:
    response = _build(_samples(_local(10), 30, 0.60), observed_peak_heart_rate_bpm=190.0)
    assert response["inputs"]["max_heart_rate_source"] == "observed_peak"
    assert float(response["inputs"]["max_heart_rate_bpm"]) == 190.0
    estimate = _build(_samples(_local(10), 30, 0.60), observed_peak_heart_rate_bpm=150.0)
    assert estimate["inputs"]["max_heart_rate_source"] == "age_estimate"
    assert float(estimate["inputs"]["max_heart_rate_bpm"]) == pytest.approx(MAX_ESTIMATE, abs=1e-4)


def test_missing_inputs_suppress_the_layer_without_invention() -> None:
    assert _build([])["missing_inputs"] == ["heart_rate_samples"]
    no_rest = _build(_samples(_local(10), 30, 0.6), resting_heart_rate_bpm=None)
    assert no_rest["available"] is False
    assert no_rest["missing_inputs"] == ["resting_heart_rate"]
    no_reference = _build(
        _samples(_local(10), 30, 0.6),
        reference=build_reference(day=DAY, timezone=ZONE, wake_at=None, sleep_onset_at=None),
    )
    assert no_reference["missing_inputs"] == ["wake_reference"]
    assert no_reference["samples"] == []
    narrow = _build(_samples(_local(10), 30, 0.6), resting_heart_rate_bpm=170.0)
    assert narrow["missing_inputs"] == ["heart_rate_reserve"]


def test_previous_evening_load_carries_into_the_selected_day() -> None:
    late = _samples(_local(23, 0, DAY - timedelta(days=1)), 30, 0.8)
    response = _build(late)
    assert _increment_at(response, _local(0)) > 0.3
