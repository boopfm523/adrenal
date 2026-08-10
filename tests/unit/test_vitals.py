from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from healthcurve.api.schemas import BloodPressureIn, WeightIn
from healthcurve.vitals.models import WeightUnit
from healthcurve.vitals.service import normalize_weight_kg

SYNTHETIC_TIME = {"local_time": "2026-08-09T08:15:00", "timezone": "Europe/London"}


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("82"), WeightUnit.KG, Decimal("82.0000")),
        (Decimal("180"), WeightUnit.LB, Decimal("81.6466")),
        (Decimal("0.1"), WeightUnit.LB, Decimal("0.0454")),
    ],
)
def test_weight_normalization_is_deterministic(
    value: Decimal, unit: WeightUnit, expected: Decimal
) -> None:
    assert normalize_weight_kg(value, unit) == expected


def test_structurally_unusual_blood_pressure_is_preserved_for_review() -> None:
    payload = BloodPressureIn.model_validate(
        {"systolic_mmhg": 40, "diastolic_mmhg": 250, "pulse_bpm": 1, "time": SYNTHETIC_TIME}
    )
    assert (payload.systolic_mmhg, payload.diastolic_mmhg) == (40, 250)


@pytest.mark.parametrize(
    "payload",
    [
        {"systolic_mmhg": 0, "diastolic_mmhg": 80, "time": SYNTHETIC_TIME},
        {"systolic_mmhg": 120, "diastolic_mmhg": 0, "time": SYNTHETIC_TIME},
    ],
)
def test_blood_pressure_requires_both_positive_components(payload: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        BloodPressureIn.model_validate(payload)


def test_weight_requires_an_explicit_positive_value_and_unit() -> None:
    with pytest.raises(ValidationError):
        WeightIn.model_validate({"value": "0", "unit": "kg", "time": SYNTHETIC_TIME})
