"""Deterministic transformations for structured vital facts."""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal

from healthcurve.vitals.models import WeightUnit

LB_TO_KG = Decimal("0.45359237")
NORMALIZED_QUANTUM = Decimal("0.0001")


def normalize_weight_kg(value: Decimal, unit: WeightUnit) -> Decimal:
    """Normalize an entered weight without replacing its original value or unit."""
    kilograms = value if unit is WeightUnit.KG else value * LB_TO_KG
    return kilograms.quantize(NORMALIZED_QUANTUM, rounding=ROUND_HALF_UP)
