from decimal import Decimal

import pytest

from healthcurve.labs.normalization import ANALYTES, NORMALIZATION_VERSION, normalize_lab_value


@pytest.mark.parametrize(
    "name,value,unit,code,normalized,normalized_unit",
    [
        ("WBC", "7.2", "K/uL", "wbc", Decimal("7.2000000000"), "10^9/L"),
        ("RBC Count", "4.5", "M/uL", "rbc", Decimal("4.5000000000"), "10^12/L"),
        ("Hgb", "13.5", "g/dL", "hemoglobin", Decimal("135.0000000000"), "g/L"),
        ("Platelet Count", "250", "x10^3/uL", "platelets", Decimal("250.0000000000"), "10^9/L"),
        (
            "Absolute Neutrophil Count",
            "3.1",
            "10^9/L",
            "neutrophils_absolute",
            Decimal("3.1000000000"),
            "10^9/L",
        ),
        ("Sodium", "140", "mEq/L", "sodium", Decimal("140.0000000000"), "mmol/L"),
        ("CO2", "24", "mmol/L", "bicarbonate", Decimal("24.0000000000"), "mmol/L"),
        ("Calcium, Total", "9.2", "mg/dL", "calcium", Decimal("2.3000000000"), "mmol/L"),
        ("Cortisol AM", "10", "mcg/dL", "cortisol", Decimal("276.0000000000"), "nmol/L"),
        ("Cortisol", "276", "nmol/L", "cortisol", Decimal("276.0000000000"), "nmol/L"),
    ],
)
def test_curated_aliases_and_units_normalize_deterministically(
    name: str,
    value: str,
    unit: str,
    code: str,
    normalized: Decimal,
    normalized_unit: str,
) -> None:
    result = normalize_lab_value(name, value, unit)
    assert result is not None
    assert result.analyte_code == code
    assert result.value == normalized
    assert result.unit == normalized_unit
    assert result.method is not None and result.method.startswith(NORMALIZATION_VERSION)


def test_unknown_analytes_and_unsupported_units_never_invent_values() -> None:
    assert normalize_lab_value("Synthetic exotic marker", "12", "widgets") is None
    unsupported = normalize_lab_value("Cortisol", "12", "mcg/24 h")
    assert unsupported is not None
    assert unsupported.analyte_code == "cortisol"
    assert unsupported.value is unsupported.unit is unsupported.method is None
    nonnumeric = normalize_lab_value("Sodium", "<120", "mmol/L")
    assert nonnumeric is not None and nonnumeric.value is None


def test_registry_aliases_are_unambiguous() -> None:
    aliases = [alias.casefold() for definition in ANALYTES for alias in definition.aliases]
    assert len(aliases) == len(set(aliases))
