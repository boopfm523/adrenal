"""Stable human-facing labels for allow-listed Garmin provider fields."""

from __future__ import annotations

from decimal import Decimal

from healthcurve.integrations.garmin.models import GarminMetricType

_FIELD_PRESENTATION: dict[str, tuple[str, str]] = {
    "totalSteps": ("Steps", "daily total"),
    "restingHeartRate": ("Resting heart rate", "daily summary"),
    "averageStressLevel": ("Stress", "daily average"),
    "lastNightAvg": ("Nightly average HRV", "previous night"),
    "avgWakingRespirationValue": ("Average waking respiration", "waking period"),
    "avgSleepRespirationValue": ("Average sleeping respiration", "sleeping period"),
    "lowestRespirationValue": ("Lowest respiration", "selected day"),
    "highestRespirationValue": ("Highest respiration", "selected day"),
}


def measurement_label(metric_type: GarminMetricType, field_name: str) -> str:
    presentation = _FIELD_PRESENTATION.get(field_name)
    if presentation is not None:
        return presentation[0]
    return metric_type.value.replace("_", " ").title()


def aggregate_period_label(field_name: str) -> str | None:
    presentation = _FIELD_PRESENTATION.get(field_name)
    return None if presentation is None else presentation[1]


def measurement_summary(
    metric_type: GarminMetricType,
    field_name: str,
    value: Decimal,
    unit: str,
) -> str:
    """Return a human-facing summary without leaking provider unit tokens."""

    label = measurement_label(metric_type, field_name)
    if metric_type is GarminMetricType.STRESS:
        display_value = format(value, "f").rstrip("0").rstrip(".")
        return f"{label}: {display_value}"
    return f"{label}: {value} {unit}"
