"""Versioned illustrative circadian context band for HealthCurve exposure models.

The band is an owner-specified educational scenario, not a demographic reference
interval, personal target, medication requirement, or adequacy test. ADR-0024 defines
the anchors, interpolation, labeling, and neutral stress behavior.
"""

from __future__ import annotations

import bisect
import math
from collections.abc import Iterable, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from healthcurve.analytics.physiology import SAMPLE_INTERVAL_MINUTES

BAND_ID: Final = "hc-circadian-context-v1"
BAND_REVISION: Final = "hc-circadian-context-v1.0.0"
SERIES_KIND: Final = "illustrative_circadian_context_band"
SERIES_NAME: Final = "Illustrative circadian context band"
SERIES_UNIT: Final = "nmol/L"
LOWER_MULTIPLIER: Final = 0.8
UPPER_MULTIPLIER: Final = 1.2
STRESS_MULTIPLIER: Final = 1.0
DISPLAY_QUANTUM: Final = Decimal("0.000000001")

ANCHORS: Final[tuple[tuple[float, float], ...]] = (
    (0.0, 2.2),
    (3.0, 6.5),
    (6.0, 20.0),
    (7.5, 22.0),
    (9.0, 17.0),
    (12.0, 12.0),
    (16.0, 7.5),
    (20.0, 4.0),
    (23.0, 2.6),
    (24.0, 2.2),
)


def _same_sign(first: float, second: float) -> bool:
    return first != 0.0 and second != 0.0 and (first > 0.0) == (second > 0.0)


def _endpoint_slope(here: float, adjacent: float, delta: float, next_delta: float) -> float:
    slope = ((2.0 * here + adjacent) * delta - here * next_delta) / (here + adjacent)
    if not _same_sign(slope, delta):
        return 0.0
    if not _same_sign(delta, next_delta) and abs(slope) > abs(3.0 * delta):
        return 3.0 * delta
    return slope


def _pchip_slopes(x: Sequence[float], y: Sequence[float]) -> list[float]:
    if len(x) != len(y) or len(x) < 2:
        raise ValueError("PCHIP requires matching x/y values and at least two anchors")
    if any(not math.isfinite(value) for value in (*x, *y)):
        raise ValueError("PCHIP anchors must be finite")
    h = [x[index + 1] - x[index] for index in range(len(x) - 1)]
    if any(width <= 0.0 for width in h):
        raise ValueError("PCHIP x anchors must be strictly increasing")
    delta = [(y[index + 1] - y[index]) / h[index] for index in range(len(h))]
    if len(x) == 2:
        return [delta[0], delta[0]]

    slopes = [0.0] * len(x)
    slopes[0] = _endpoint_slope(h[0], h[1], delta[0], delta[1])
    slopes[-1] = _endpoint_slope(h[-1], h[-2], delta[-1], delta[-2])
    for index in range(1, len(x) - 1):
        previous = delta[index - 1]
        following = delta[index]
        if not _same_sign(previous, following):
            slopes[index] = 0.0
            continue
        first_weight = 2.0 * h[index] + h[index - 1]
        second_weight = h[index] + 2.0 * h[index - 1]
        slopes[index] = (first_weight + second_weight) / (
            first_weight / previous + second_weight / following
        )
    return slopes


def pchip_value(x: Sequence[float], y: Sequence[float], value: float) -> float:
    """Evaluate shape-preserving piecewise cubic Hermite interpolation."""
    if not math.isfinite(value):
        raise ValueError("PCHIP sample position must be finite")
    slopes = _pchip_slopes(x, y)
    if value <= x[0]:
        return y[0]
    if value >= x[-1]:
        return y[-1]
    index = bisect.bisect_right(x, value) - 1
    width = x[index + 1] - x[index]
    position = (value - x[index]) / width
    h00 = (2.0 * position**3) - (3.0 * position**2) + 1.0
    h10 = position**3 - (2.0 * position**2) + position
    h01 = (-2.0 * position**3) + (3.0 * position**2)
    h11 = position**3 - position**2
    result = (
        h00 * y[index]
        + h10 * width * slopes[index]
        + h01 * y[index + 1]
        + h11 * width * slopes[index + 1]
    )
    lower = min(y[index], y[index + 1])
    upper = max(y[index], y[index + 1])
    if result < lower and math.isclose(result, lower, abs_tol=1e-12):
        return lower
    if result > upper and math.isclose(result, upper, abs_tol=1e-12):
        return upper
    if not math.isfinite(result) or result < lower or result > upper:
        raise ValueError("PCHIP interpolation overshot its anchor interval")
    return result


def _display_decimal(value: float) -> Decimal:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("invalid circadian context result")
    return Decimal(str(value)).quantize(DISPLAY_QUANTUM)


def band_values_at_local_hour(local_hour: float) -> tuple[Decimal, Decimal, Decimal]:
    hours = tuple(anchor[0] for anchor in ANCHORS)
    centers = tuple(anchor[1] for anchor in ANCHORS)
    center = pchip_value(hours, centers, local_hour)
    return (
        _display_decimal(center),
        _display_decimal(center * LOWER_MULTIPLIER),
        _display_decimal(center * UPPER_MULTIPLIER),
    )


def _default_instants(start: datetime, end: datetime) -> list[datetime]:
    cursor = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    step = timedelta(minutes=SAMPLE_INTERVAL_MINUTES)
    instants = []
    while cursor < end_utc:
        instants.append(cursor)
        cursor += step
    instants.append(end_utc)
    return instants


def _local_hour(instant: datetime, *, day: date, zone: ZoneInfo, end: datetime) -> float:
    instant_utc = instant.astimezone(UTC)
    if instant_utc == end.astimezone(UTC):
        return 24.0
    local = instant_utc.astimezone(zone)
    if local.date() != day:
        raise ValueError("band sample instant falls outside the selected local day")
    return (
        local.hour
        + local.minute / 60.0
        + local.second / 3600.0
        + local.microsecond / 3_600_000_000.0
    )


def build_band(
    *,
    day: date,
    timezone: str,
    sample_instants: Iterable[datetime] | None = None,
    recorded_episode_count: int = 0,
    missing_episode_severity_count: int = 0,
) -> dict[str, object]:
    """Build a timezone-correct default-off illustrative band for one local day."""
    if recorded_episode_count < 0 or missing_episode_severity_count < 0:
        raise ValueError("episode context counts must be nonnegative")
    if missing_episode_severity_count > recorded_episode_count:
        raise ValueError("missing episode severity cannot exceed episode count")

    zone = ZoneInfo(timezone)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    start_utc = start.astimezone(UTC)
    end_utc = end.astimezone(UTC)
    if sample_instants is None:
        instants = _default_instants(start, end)
    else:
        instants = sorted({instant.astimezone(UTC) for instant in sample_instants})
        if not instants or instants[0] < start_utc or instants[-1] > end_utc:
            raise ValueError("band sample instants must stay within the selected local day")

    samples = []
    for instant in instants:
        local = instant.astimezone(zone)
        center, lower, upper = band_values_at_local_hour(
            _local_hour(instant, day=day, zone=zone, end=end)
        )
        offset = local.utcoffset()
        samples.append(
            {
                "occurred_at": instant,
                "local_time": local.replace(tzinfo=None),
                "utc_offset_minutes": int((offset or timedelta()).total_seconds() // 60),
                "center_nmol_l": center,
                "lower_nmol_l": lower,
                "upper_nmol_l": upper,
            }
        )

    return {
        "date": day,
        "timezone": timezone,
        "day_start": start_utc,
        "day_end": end_utc,
        "elapsed_hours": Decimal(str((end_utc - start_utc).total_seconds() / 3600)),
        "series_kind": SERIES_KIND,
        "series_name": SERIES_NAME,
        "series_unit": SERIES_UNIT,
        "default_visible": False,
        "safety_label": (
            "Illustrative owner-specified circadian context—not a normal range, personal "
            "target, medication requirement, adequacy test, or dosing guide."
        ),
        "band": {
            "id": BAND_ID,
            "revision": BAND_REVISION,
            "interpolation": "pchip-no-overshoot",
            "lower_multiplier": Decimal(str(LOWER_MULTIPLIER)),
            "upper_multiplier": Decimal(str(UPPER_MULTIPLIER)),
            "anchor_origin": "owner_supplied_synthetic_scenario",
            "healthy_rhythm_evidence_scope": "shape_and_phase_context_only",
            "personalized": False,
            "body_context_used": False,
            "demographic_reference_interval": False,
            "references": [
                "https://doi.org/10.1210/jc.2008-2380",
                "https://pmc.ncbi.nlm.nih.gov/articles/PMC2684472/",
            ],
            "anchors": [
                {"local_hour": Decimal(str(hour)), "center_nmol_l": Decimal(str(center))}
                for hour, center in ANCHORS
            ],
        },
        "recorded_stress_context": {
            "episode_count": recorded_episode_count,
            "missing_severity_count": missing_episode_severity_count,
            "multiplier": Decimal(str(STRESS_MULTIPLIER)),
            "applied_to_band": False,
            "applied_to_drug_model": False,
            "reason": "No validated individual stress-to-cortisol-demand mapping in v2.0.0.",
        },
        "samples": samples,
    }
