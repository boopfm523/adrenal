from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import ValidationError

from healthcurve.api.schemas import BloodPressureIn, TemperatureIn, WeightIn
from healthcurve.vitals.models import TemperatureUnit, WeightUnit
from healthcurve.vitals.service import (
    display_temperature_c,
    display_temperature_f,
    display_weight_lb,
    normalize_temperature_c,
    normalize_weight_kg,
    temperature_in_range,
)

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


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("180"), WeightUnit.LB, Decimal("180.0")),
        (Decimal("83.1"), WeightUnit.KG, Decimal("183.2")),
        (Decimal("1.05"), WeightUnit.LB, Decimal("1.1")),
    ],
)
def test_weight_pounds_presentation_is_deterministic_and_half_up(
    value: Decimal, unit: WeightUnit, expected: Decimal
) -> None:
    assert display_weight_lb(value, unit) == expected


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


@pytest.mark.parametrize(
    ("value", "unit", "normalized", "display_f", "display_c"),
    [
        (
            Decimal("38"),
            TemperatureUnit.CELSIUS,
            Decimal("38.00"),
            Decimal("100.4"),
            Decimal("38.0"),
        ),
        (
            Decimal("98.6"),
            TemperatureUnit.FAHRENHEIT,
            Decimal("37.00"),
            Decimal("98.6"),
            Decimal("37.0"),
        ),
        (
            Decimal("37.25"),
            TemperatureUnit.CELSIUS,
            Decimal("37.25"),
            Decimal("99.1"),
            Decimal("37.3"),
        ),
    ],
)
def test_temperature_conversion_is_deterministic_and_fahrenheit_first(
    value: Decimal,
    unit: TemperatureUnit,
    normalized: Decimal,
    display_f: Decimal,
    display_c: Decimal,
) -> None:
    assert normalize_temperature_c(value, unit) == normalized
    assert display_temperature_f(value, unit) == display_f
    assert display_temperature_c(value, unit) == display_c


@pytest.mark.parametrize(
    ("value", "unit", "expected"),
    [
        (Decimal("77"), TemperatureUnit.FAHRENHEIT, True),
        (Decimal("113"), TemperatureUnit.FAHRENHEIT, True),
        (Decimal("25"), TemperatureUnit.CELSIUS, True),
        (Decimal("76.9"), TemperatureUnit.FAHRENHEIT, False),
        (Decimal("45.1"), TemperatureUnit.CELSIUS, False),
    ],
)
def test_temperature_bounds_are_structural_not_diagnostic(
    value: Decimal, unit: TemperatureUnit, expected: bool
) -> None:
    assert temperature_in_range(value, unit) is expected


def test_temperature_schema_requires_explicit_unit() -> None:
    with pytest.raises(ValidationError):
        TemperatureIn.model_validate({"value": "98.6", "time": SYNTHETIC_TIME})
