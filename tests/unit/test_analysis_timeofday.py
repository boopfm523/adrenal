"""Circular clock-time statistics (ADR-0036). Synthetic values only."""

from __future__ import annotations

import pytest

from healthcurve.analysis.timeofday import clock_label, summarize_clock_minutes


def _minutes(clock: str) -> int:
    hours, minutes = clock.split(":")
    return int(hours) * 60 + int(minutes)


def test_times_either_side_of_midnight_average_to_midnight() -> None:
    summary = summarize_clock_minutes([_minutes("23:30"), _minutes("00:30")])
    assert summary.count == 2
    assert summary.mean_clock == "00:00"
    assert summary.median_clock == "00:00"
    assert summary.earliest_clock == "23:30"
    assert summary.latest_clock == "00:30"
    assert summary.spread_minutes == pytest.approx(30.0, abs=0.2)


def test_ordinary_morning_times_match_arithmetic_expectations() -> None:
    summary = summarize_clock_minutes([_minutes(value) for value in ("07:00", "07:30", "08:00")])
    assert (summary.mean_clock, summary.median_clock) == ("07:30", "07:30")
    assert (summary.earliest_clock, summary.latest_clock) == ("07:00", "08:00")


def test_median_uses_the_middle_pair_for_even_counts() -> None:
    summary = summarize_clock_minutes([_minutes(v) for v in ("22:50", "23:10", "23:40", "00:20")])
    assert summary.median_clock == "23:25"
    assert summary.earliest_clock == "22:50"
    assert summary.latest_clock == "00:20"


def test_missing_values_produce_no_clock_times() -> None:
    summary = summarize_clock_minutes([])
    assert summary.count == 0
    assert summary.mean_clock is None
    assert summary.as_data()["spread_minutes"] is None


def test_evenly_scattered_times_have_no_meaningful_average() -> None:
    summary = summarize_clock_minutes([_minutes("00:00"), _minutes("12:00")])
    assert summary.count == 2
    assert summary.mean_clock is None
    assert summary.concentration == 0.0


def test_single_value_and_label_wraparound() -> None:
    summary = summarize_clock_minutes([_minutes("06:45")])
    assert (summary.mean_clock, summary.spread_minutes) == ("06:45", 0.0)
    assert clock_label(1_440) == "00:00"
    assert clock_label(-15) == "23:45"
