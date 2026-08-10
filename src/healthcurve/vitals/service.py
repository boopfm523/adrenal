"""Deterministic transformations for structured vital facts."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from healthcurve.vitals.models import WeightUnit

LB_TO_KG = Decimal("0.45359237")
NORMALIZED_QUANTUM = Decimal("0.0001")
DISPLAY_LB_QUANTUM = Decimal("0.1")


def normalize_weight_kg(value: Decimal, unit: WeightUnit) -> Decimal:
    """Normalize an entered weight without replacing its original value or unit."""
    kilograms = value if unit is WeightUnit.KG else value * LB_TO_KG
    return kilograms.quantize(NORMALIZED_QUANTUM, rounding=ROUND_HALF_UP)


def display_weight_lb(value: Decimal, unit: WeightUnit) -> Decimal:
    """Return the pounds-first presentation value without altering the fact."""
    pounds = value if unit is WeightUnit.LB else value / LB_TO_KG
    return pounds.quantize(DISPLAY_LB_QUANTUM, rounding=ROUND_HALF_UP)
