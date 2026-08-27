"""Deterministic transformations for structured vital facts."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from healthcurve.vitals.models import TemperatureUnit, WeightUnit

LB_TO_KG = Decimal("0.45359237")
NORMALIZED_QUANTUM = Decimal("0.0001")
DISPLAY_LB_QUANTUM = Decimal("0.1")
DISPLAY_TEMPERATURE_QUANTUM = Decimal("0.1")


def normalize_weight_kg(value: Decimal, unit: WeightUnit) -> Decimal:
    """Normalize an entered weight without replacing its original value or unit."""
    kilograms = value if unit is WeightUnit.KG else value * LB_TO_KG
    return kilograms.quantize(NORMALIZED_QUANTUM, rounding=ROUND_HALF_UP)


def display_weight_lb(value: Decimal, unit: WeightUnit) -> Decimal:
    """Return the pounds-first presentation value without altering the fact."""
    pounds = value if unit is WeightUnit.LB else value / LB_TO_KG
    return pounds.quantize(DISPLAY_LB_QUANTUM, rounding=ROUND_HALF_UP)


def normalize_temperature_c(value: Decimal, unit: TemperatureUnit) -> Decimal:
    """Normalize an entered temperature without replacing its original fact."""
    celsius = value if unit is TemperatureUnit.CELSIUS else (value - 32) * 5 / 9
    return celsius.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def display_temperature_f(value: Decimal, unit: TemperatureUnit) -> Decimal:
    """Return Fahrenheit-first presentation rounded half up to one decimal."""
    fahrenheit = value if unit is TemperatureUnit.FAHRENHEIT else value * 9 / 5 + 32
    return fahrenheit.quantize(DISPLAY_TEMPERATURE_QUANTUM, rounding=ROUND_HALF_UP)


def display_temperature_c(value: Decimal, unit: TemperatureUnit) -> Decimal:
    """Return Celsius presentation rounded half up to one decimal."""
    return normalize_temperature_c(value, unit).quantize(
        DISPLAY_TEMPERATURE_QUANTUM, rounding=ROUND_HALF_UP
    )


def temperature_in_range(value: Decimal, unit: TemperatureUnit) -> bool:
    """Accept a broad structural human-measurement range without diagnosis."""
    if not value.is_finite():
        return False
    normalized = normalize_temperature_c(value, unit)
    return normalized.is_finite() and Decimal("25") <= normalized <= Decimal("45")


def infer_temperature_unit(value: Decimal) -> TemperatureUnit | None:
    """Infer a unit only when exactly one supported structural range accepts it."""
    matching_units = [unit for unit in TemperatureUnit if temperature_in_range(value, unit)]
    return matching_units[0] if len(matching_units) == 1 else None
