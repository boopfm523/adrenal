"""Wake-anchored healthy-adult cortisol reference context.

This deterministic reference engine is a visualization and hypothesis-generating
aid. It is not a personal target, cortisol measurement, medication adequacy test,
or dosing guide. ADR-0026 defines how this independent reference is used.
"""

from __future__ import annotations

import math
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from healthcurve.analytics.circadian_context import pchip_value
from healthcurve.analytics.physiology import SAMPLE_INTERVAL_MINUTES

REFERENCE_ID: Final = "hc-wake-reference-v1"
REFERENCE_REVISION: Final = "hc-wake-reference-v1.0.0"
BINDING_REVISION: Final = "one-site-cbg-linear-albumin-v1"
SERIES_KIND: Final = "wake_anchored_cortisol_reference"
SERIES_UNIT: Final = "nmol/L"
DISPLAY_QUANTUM: Final = Decimal("0.000000001")

CBG_G: Final = 700.0
CBG_K: Final = 0.030
N_ALB: Final = 1.74

DAY_ANCHORS_TAU: Final[tuple[tuple[float, float], ...]] = (
    (0.00, 15.52),
    (0.50, 15.80),
    (1.00, 15.00),
    (2.00, 11.60),
    (3.00, 9.20),
    (4.00, 7.30),
    (6.00, 5.00),
    (8.00, 3.90),
    (10.00, 2.90),
    (12.00, 1.97),
    (14.00, 1.62),
)

NIGHT_ANCHORS_NU: Final[tuple[tuple[float, float], ...]] = (
    (0.00, 1.25),
    (2.00, 1.13),
    (3.25, 1.20),
    (4.00, 1.70),
    (5.00, 3.40),
    (6.00, 6.50),
)

CAR_DELAY_H: Final = 35.0 / 60.0
CAR_AMPLITUDE: Final = 0.46
CAR_RISE_SD_H: Final = 13.0 / 60.0
CAR_FALL_SD_H: Final = 40.0 / 60.0

MEAL_PULSES: Final[Mapping[str, tuple[float, float, float]]] = {
    "breakfast": (0.05, 1.00, 0.55),
    "lunch": (0.30, 1.00, 0.58),
    "dinner": (0.19, 1.00, 0.60),
}

SIGMA_TAU: Final[tuple[tuple[float, float], ...]] = (
    (-6.0, 0.50),
    (-3.0, 0.50),
    (-1.5, 0.44),
    (0.0, 0.42),
    (2.0, 0.42),
    (4.0, 0.42),
    (7.0, 0.45),
    (10.0, 0.47),
    (13.0, 0.49),
    (16.0, 0.50),
)

PERCENTILE_Z: Final[Mapping[str, float]] = {
    "p5": -1.645,
    "p25": -0.6744897501960817,
    "p50": 0.0,
    "p75": 0.6744897501960817,
    "p95": 1.645,
}

K_FREE: Final = 1.605
WAKE_DECAY_PER_H: Final = 0.907
WAKE_REFERENCE_H: Final = 7.0

REFERENCES: Final[tuple[dict[str, object], ...]] = (
    {
        "citation": "Miller R et al. CIRCORT database. Psychoneuroendocrinology. 2016.",
        "pmid": "27448524",
    },
    {
        "citation": "Van Cauter E et al. J Clin Endocrinol Metab. 1996;81:2468-73.",
        "pmid": "8675562",
    },
    {
        "citation": "Backlund N et al. J Clin Endocrinol Metab. 2025;110:1218.",
        "url": "https://academic.oup.com/jcem/article/110/5/1218/7712629",
    },
)


def _decimal(value: float) -> Decimal:
    if not math.isfinite(value) or value < 0.0:
        raise ValueError("reference values must be finite and nonnegative")
    return Decimal(str(value)).quantize(DISPLAY_QUANTUM)


def total_from_free(free_nmol_l: float) -> float:
    """Convert serum free cortisol to serum total using saturable CBG binding."""
    if not math.isfinite(free_nmol_l) or free_nmol_l < 0.0:
        raise ValueError("free cortisol must be finite and nonnegative")
    return free_nmol_l * (1.0 + N_ALB) + (CBG_G * CBG_K * free_nmol_l / (1.0 + CBG_K * free_nmol_l))


def free_from_total(total_nmol_l: float) -> float:
    """Invert the monotone CBG binding equation without a scientific runtime."""
    if not math.isfinite(total_nmol_l) or total_nmol_l < 0.0:
        raise ValueError("total cortisol must be finite and nonnegative")
    if total_nmol_l == 0.0:
        return 0.0

    lower = 0.0
    upper = total_nmol_l + 1.0
    for _ in range(100):
        midpoint = (lower + upper) / 2.0
        if total_from_free(midpoint) < total_nmol_l:
            lower = midpoint
        else:
            upper = midpoint
    return (lower + upper) / 2.0


def _local_clock_hour(value: datetime, zone: ZoneInfo) -> float:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("reference observation times must include a timezone")
    local = value.astimezone(zone)
    return (
        local.hour
        + local.minute / 60.0
        + local.second / 3600.0
        + local.microsecond / 3_600_000_000.0
    )


def _pulse(
    local_hour: float,
    *,
    peak_hour: float,
    amplitude: float,
    fall_sd: float,
    rise_sd: float | None = None,
) -> float:
    distance = (local_hour - peak_hour + 12.0) % 24.0 - 12.0
    sd = rise_sd if distance < 0.0 and rise_sd is not None else fall_sd
    return amplitude * math.exp(-(distance**2) / (2.0 * sd**2))


def _age_sex_factor(age_years: float, sex: str) -> tuple[float, float]:
    if not math.isfinite(age_years) or not 0.0 <= age_years <= 120.0:
        raise ValueError("age must be between 0 and 120 years")
    normalized_sex = sex.strip().lower()
    if normalized_sex in {"m", "male"}:
        sex_factor = 1.03
    elif normalized_sex in {"f", "female"}:
        sex_factor = 1.0
    else:
        raise ValueError("sex must be male/M or female/F for this reference")
    decades_over_50 = max(0.0, age_years - 50.0) / 10.0
    return (
        sex_factor * (1.0 + 0.02 * decades_over_50),
        sex_factor * (1.0 + 0.06 * decades_over_50),
    )


def _linear_value(anchors: Sequence[tuple[float, float]], value: float) -> float:
    if value <= anchors[0][0]:
        return anchors[0][1]
    if value >= anchors[-1][0]:
        return anchors[-1][1]
    for index in range(len(anchors) - 1):
        left_x, left_y = anchors[index]
        right_x, right_y = anchors[index + 1]
        if left_x <= value <= right_x:
            position = (value - left_x) / (right_x - left_x)
            return left_y + position * (right_y - left_y)
    raise ValueError("linear interpolation position was outside its anchors")


def _periodic_log_anchors(
    wake_hour: float, sleep_onset_hour: float
) -> tuple[list[float], list[float]]:
    points = [(wake_hour + tau, value) for tau, value in DAY_ANCHORS_TAU]
    points.extend((sleep_onset_hour + nu, value) for nu, value in NIGHT_ANCHORS_NU)

    last_nu, last_value = NIGHT_ANCHORS_NU[-1]
    bridge_start = sleep_onset_hour + last_nu
    wake_absolute = wake_hour + 24.0 if wake_hour < bridge_start else wake_hour
    if wake_absolute - bridge_start > 0.25:
        midpoint = bridge_start + 0.6 * (wake_absolute - bridge_start)
        bridge_value = last_value + 0.62 * (DAY_ANCHORS_TAU[0][1] - last_value)
        points.append((midpoint, bridge_value))

    clock_points = sorted((hour % 24.0, value) for hour, value in points)
    extended = [
        (hour + shift, value) for shift in (-24.0, 0.0, 24.0) for hour, value in clock_points
    ]
    extended.sort()
    unique = []
    for hour, value in extended:
        if unique and hour - unique[-1][0] <= 1e-6:
            continue
        unique.append((hour, value))
    return (
        [hour for hour, _ in unique],
        [math.log(value) for _, value in unique],
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


def _validate_sample_instants(
    sample_instants: Iterable[datetime], *, start: datetime, end: datetime
) -> list[datetime]:
    values = list(sample_instants)
    if any(value.tzinfo is None or value.utcoffset() is None for value in values):
        raise ValueError("reference sample instants must include a timezone")
    instants = sorted({value.astimezone(UTC) for value in values})
    if not instants:
        raise ValueError("reference sample instants cannot be empty")
    if instants[0] < start.astimezone(UTC) or instants[-1] > end.astimezone(UTC):
        raise ValueError("reference sample instants must stay within the selected local day")
    return instants


def _missing_reference(*, day: date, timezone: str, missing_inputs: list[str]) -> dict[str, object]:
    return {
        "available": False,
        "date": day,
        "timezone": timezone,
        "series_kind": SERIES_KIND,
        "series_unit": SERIES_UNIT,
        "reference": {
            "id": REFERENCE_ID,
            "revision": REFERENCE_REVISION,
            "binding_revision": BINDING_REVISION,
        },
        "missing_inputs": missing_inputs,
        "samples": [],
        "safety_label": (
            "No reference was generated because wake or sleep timing was unavailable; "
            "HealthCurve does not invent missing timing facts."
        ),
    }


def build_reference(
    *,
    day: date,
    timezone: str,
    wake_at: datetime | None,
    sleep_onset_at: datetime | None,
    meals: Mapping[str, datetime] | None = None,
    age_years: float = 47.0,
    sex: str = "M",
    wake_amplitude_effect: bool = True,
    sample_instants: Iterable[datetime] | None = None,
) -> dict[str, object]:
    """Generate a non-cached reference on the selected local day's real duration."""
    zone = ZoneInfo(timezone)
    missing_inputs = [
        name
        for name, value in (("wake_at", wake_at), ("sleep_onset_at", sleep_onset_at))
        if value is None
    ]
    if missing_inputs:
        return _missing_reference(day=day, timezone=timezone, missing_inputs=missing_inputs)
    if wake_at is None or sleep_onset_at is None:  # narrowed above; keeps strict typing explicit
        raise AssertionError("unreachable missing reference input")

    wake_hour = _local_clock_hour(wake_at, zone)
    sleep_onset_hour = _local_clock_hour(sleep_onset_at, zone)
    if wake_at.astimezone(zone).date() != day:
        raise ValueError("wake observation must belong to the selected local day")
    if sleep_onset_at.astimezone(zone).date() not in {day - timedelta(days=1), day}:
        raise ValueError("sleep onset must belong to the selected local day or prior night")

    meal_hours: dict[str, float] = {}
    for role, observed_at in (meals or {}).items():
        normalized_role = role.strip().lower()
        if normalized_role not in MEAL_PULSES:
            raise ValueError(f"unsupported meal role: {role}")
        if observed_at.astimezone(zone).date() != day:
            raise ValueError("meal observations must belong to the selected local day")
        meal_hours[normalized_role] = _local_clock_hour(observed_at, zone)

    peak_factor, nadir_factor = _age_sex_factor(age_years, sex)
    start = datetime.combine(day, time.min, tzinfo=zone)
    end = datetime.combine(day + timedelta(days=1), time.min, tzinfo=zone)
    if sample_instants is None:
        instants = _default_instants(start, end)
    else:
        instants = _validate_sample_instants(sample_instants, start=start, end=end)

    local_hours = []
    for instant in instants:
        if instant == end.astimezone(UTC):
            local_hours.append(24.0)
        else:
            local_hours.append(_local_clock_hour(instant, zone))

    anchor_hours, anchor_logs = _periodic_log_anchors(wake_hour, sleep_onset_hour)
    medians = []
    for local_hour in local_hours:
        base = math.exp(pchip_value(anchor_hours, anchor_logs, local_hour))
        multiplier = 1.0 + _pulse(
            local_hour,
            peak_hour=wake_hour + CAR_DELAY_H,
            amplitude=CAR_AMPLITUDE,
            fall_sd=CAR_FALL_SD_H,
            rise_sd=CAR_RISE_SD_H,
        )
        for role, meal_hour in meal_hours.items():
            amplitude, lag, sd = MEAL_PULSES[role]
            multiplier += _pulse(
                local_hour,
                peak_hour=meal_hour + lag,
                amplitude=amplitude,
                fall_sd=sd,
            )
        medians.append(base * multiplier)

    minimum = min(medians)
    maximum = max(medians)
    span = maximum - minimum
    if span <= 0.0:
        raise ValueError("reference median has no usable daily variation")
    medians = [
        median * (nadir_factor + (peak_factor - nadir_factor) * ((median - minimum) / span))
        for median in medians
    ]
    if wake_amplitude_effect:
        wake_multiplier = WAKE_DECAY_PER_H ** (wake_hour - WAKE_REFERENCE_H)
        medians = [median * wake_multiplier for median in medians]

    samples = []
    sigma_anchors = tuple(SIGMA_TAU)
    for instant, local_hour, median in zip(instants, local_hours, medians, strict=True):
        hours_since_wake = (local_hour - wake_hour + 12.0) % 24.0 - 12.0
        sigma_log = _linear_value(sigma_anchors, hours_since_wake)
        local = instant.astimezone(zone)
        offset = local.utcoffset() or timedelta()
        sample: dict[str, object] = {
            "occurred_at": instant,
            "local_time": local.replace(tzinfo=None),
            "utc_offset_minutes": int(offset.total_seconds() // 60),
            "hour_local": Decimal(str(local_hour)).quantize(DISPLAY_QUANTUM),
            "hours_since_wake": Decimal(str(hours_since_wake)).quantize(DISPLAY_QUANTUM),
            "sigma_log": Decimal(str(sigma_log)).quantize(DISPLAY_QUANTUM),
        }
        for label, z_value in PERCENTILE_Z.items():
            internal = median * math.exp(z_value * sigma_log)
            free = internal * K_FREE
            sample[f"serum_free_{label}_nmol_l"] = _decimal(free)
            sample[f"serum_total_{label}_nmol_l"] = _decimal(total_from_free(free))
        samples.append(sample)

    return {
        "available": True,
        "date": day,
        "timezone": timezone,
        "day_start": start.astimezone(UTC),
        "day_end": end.astimezone(UTC),
        "elapsed_hours": Decimal(
            str((end.astimezone(UTC) - start.astimezone(UTC)).total_seconds() / 3600)
        ),
        "series_kind": SERIES_KIND,
        "series_unit": SERIES_UNIT,
        "reference": {
            "id": REFERENCE_ID,
            "revision": REFERENCE_REVISION,
            "binding_revision": BINDING_REVISION,
            "source_module": "owner-supplied cortisol_reference.py",
            "sample_interval_minutes": SAMPLE_INTERVAL_MINUTES,
            "percentiles": list(PERCENTILE_Z),
            "default_band": ["p5", "p95"],
            "references": [dict(reference) for reference in REFERENCES],
        },
        "assumptions": {
            "healthy_adult_population_context_only": True,
            "wake_at": wake_at,
            "sleep_onset_at": sleep_onset_at,
            "age_years": Decimal(str(age_years)),
            "sex": sex,
            "wake_amplitude_association_applied": wake_amplitude_effect,
            "observed_meals": dict(meals or {}),
            "unobserved_meals_invented": False,
            "pre_wake_gap_expected": True,
        },
        "missing_inputs": [],
        "safety_label": (
            "Healthy-adult reference context only—not a personal target, cortisol "
            "measurement, medication adequacy test, alert, or dosing guide."
        ),
        "samples": samples,
    }
