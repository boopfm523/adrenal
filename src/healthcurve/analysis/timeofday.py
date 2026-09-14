"""Circular statistics for local clock times (bedtimes, wake times, dose times).

Clock times wrap at midnight, so an arithmetic mean of 23:30 and 00:30 would be noon.
These functions treat the day as a circle and report the circular mean, a median and
range measured around that mean, and an angular spread in minutes.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Final

MINUTES_PER_DAY: Final = 1_440
#: Below this mean resultant length the times are spread around the clock so evenly that
#: no average clock time is meaningful.
MIN_CONCENTRATION: Final = 0.05


@dataclass(frozen=True, slots=True)
class ClockTimeSummary:
    count: int
    mean_clock: str | None
    median_clock: str | None
    earliest_clock: str | None
    latest_clock: str | None
    spread_minutes: float | None
    concentration: float | None

    def as_data(self) -> dict[str, object]:
        return {
            "count": self.count,
            "mean_clock": self.mean_clock,
            "median_clock": self.median_clock,
            "earliest_clock": self.earliest_clock,
            "latest_clock": self.latest_clock,
            "spread_minutes": self.spread_minutes,
            "concentration": self.concentration,
        }


def clock_label(minutes: float) -> str:
    whole = round(minutes) % MINUTES_PER_DAY
    return f"{whole // 60:02d}:{whole % 60:02d}"


def summarize_clock_minutes(minutes: Sequence[float]) -> ClockTimeSummary:
    """Summarize minutes after local midnight (0 <= value < 1440)."""

    values = [float(value) % MINUTES_PER_DAY for value in minutes]
    if not values:
        return ClockTimeSummary(0, None, None, None, None, None, None)
    angles = [2 * math.pi * value / MINUTES_PER_DAY for value in values]
    sin_mean = sum(math.sin(angle) for angle in angles) / len(angles)
    cos_mean = sum(math.cos(angle) for angle in angles) / len(angles)
    concentration = math.hypot(sin_mean, cos_mean)
    if concentration < MIN_CONCENTRATION:
        return ClockTimeSummary(len(values), None, None, None, None, None, round(concentration, 3))

    mean = (math.atan2(sin_mean, cos_mean) % (2 * math.pi)) * MINUTES_PER_DAY / (2 * math.pi)
    offsets = sorted(
        (value - mean + MINUTES_PER_DAY / 2) % MINUTES_PER_DAY - MINUTES_PER_DAY / 2
        for value in values
    )
    middle = len(offsets) // 2
    median_offset = (
        offsets[middle] if len(offsets) % 2 else (offsets[middle - 1] + offsets[middle]) / 2
    )
    spread = math.sqrt(-2 * math.log(min(concentration, 1.0))) * MINUTES_PER_DAY / (2 * math.pi)
    return ClockTimeSummary(
        count=len(values),
        mean_clock=clock_label(mean),
        median_clock=clock_label(mean + median_offset),
        earliest_clock=clock_label(mean + offsets[0]),
        latest_clock=clock_label(mean + offsets[-1]),
        spread_minutes=round(spread, 1),
        concentration=round(concentration, 3),
    )
