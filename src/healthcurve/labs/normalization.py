"""Small, versioned, deterministic laboratory normalization registry.

Original source strings always remain the fact. These rules add optional derived
fields for a deliberately curated CBC, acute-chemistry, and cortisol allow-list.
They do not calculate abnormality, interpret a result, or recommend treatment.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import ROUND_HALF_EVEN, Decimal, InvalidOperation
from typing import Final

NORMALIZATION_VERSION: Final = "hc-lab-normalization-v1"
_SCALE: Final = Decimal("0.0000000001")
_MAX_ABS: Final = Decimal("99999999999999")


@dataclass(frozen=True, slots=True)
class AnalyteDefinition:
    code: str
    display_name: str
    aliases: tuple[str, ...]
    canonical_unit: str
    unit_factors: tuple[tuple[str, Decimal], ...]


@dataclass(frozen=True, slots=True)
class NormalizedLabValue:
    analyte_code: str
    analyte_name: str
    value: Decimal | None
    unit: str | None
    method: str | None


def _definition(
    code: str,
    display: str,
    aliases: tuple[str, ...],
    unit: str,
    factors: dict[str, str],
) -> AnalyteDefinition:
    return AnalyteDefinition(
        code,
        display,
        aliases,
        unit,
        tuple((source, Decimal(factor)) for source, factor in factors.items()),
    )


# Cortisol serum conversion uses Mayo Clinic Laboratories' published factor:
# mcg/dL x 27.6 = nmol/L. Prefix-only conversions use exact SI powers of ten.
ANALYTES: Final = (
    _definition(
        "wbc",
        "White blood cell count",
        ("wbc", "wbc count", "white blood cell count", "leukocytes"),
        "10^9/L",
        {
            "10^9/L": "1",
            "x10^9/L": "1",
            "10*9/L": "1",
            "K/uL": "1",
            "10^3/uL": "1",
            "x10^3/uL": "1",
        },
    ),
    _definition(
        "rbc",
        "Red blood cell count",
        ("rbc", "rbc count", "red blood cell count", "erythrocytes"),
        "10^12/L",
        {
            "10^12/L": "1",
            "x10^12/L": "1",
            "10*12/L": "1",
            "M/uL": "1",
            "10^6/uL": "1",
            "x10^6/uL": "1",
        },
    ),
    _definition(
        "hemoglobin",
        "Hemoglobin",
        ("hemoglobin", "haemoglobin", "hgb", "hb"),
        "g/L",
        {"g/L": "1", "g/dL": "10"},
    ),
    _definition("hematocrit", "Hematocrit", ("hematocrit", "haematocrit", "hct"), "%", {"%": "1"}),
    _definition(
        "mcv", "Mean corpuscular volume", ("mcv", "mean corpuscular volume"), "fL", {"fL": "1"}
    ),
    _definition(
        "mch",
        "Mean corpuscular hemoglobin",
        ("mch", "mean corpuscular hemoglobin", "mean corpuscular haemoglobin"),
        "pg",
        {"pg": "1"},
    ),
    _definition(
        "mchc",
        "Mean corpuscular hemoglobin concentration",
        (
            "mchc",
            "mean corpuscular hemoglobin concentration",
            "mean corpuscular haemoglobin concentration",
        ),
        "g/dL",
        {"g/dL": "1", "g/L": "0.1"},
    ),
    _definition(
        "rdw",
        "Red cell distribution width",
        ("rdw", "rdw-cv", "red cell distribution width"),
        "%",
        {"%": "1"},
    ),
    _definition(
        "platelets",
        "Platelet count",
        ("platelets", "platelet count", "plt", "plts"),
        "10^9/L",
        {
            "10^9/L": "1",
            "x10^9/L": "1",
            "10*9/L": "1",
            "K/uL": "1",
            "10^3/uL": "1",
            "x10^3/uL": "1",
        },
    ),
    _definition("mpv", "Mean platelet volume", ("mpv", "mean platelet volume"), "fL", {"fL": "1"}),
    _definition(
        "neutrophils_absolute",
        "Absolute neutrophil count",
        ("absolute neutrophils", "neutrophils absolute", "absolute neutrophil count", "anc"),
        "10^9/L",
        {"10^9/L": "1", "x10^9/L": "1", "K/uL": "1", "10^3/uL": "1"},
    ),
    _definition(
        "lymphocytes_absolute",
        "Absolute lymphocyte count",
        ("absolute lymphocytes", "lymphocytes absolute", "absolute lymphocyte count", "alc"),
        "10^9/L",
        {"10^9/L": "1", "x10^9/L": "1", "K/uL": "1", "10^3/uL": "1"},
    ),
    _definition(
        "monocytes_absolute",
        "Absolute monocyte count",
        ("absolute monocytes", "monocytes absolute", "absolute monocyte count"),
        "10^9/L",
        {"10^9/L": "1", "x10^9/L": "1", "K/uL": "1", "10^3/uL": "1"},
    ),
    _definition(
        "eosinophils_absolute",
        "Absolute eosinophil count",
        ("absolute eosinophils", "eosinophils absolute", "absolute eosinophil count"),
        "10^9/L",
        {"10^9/L": "1", "x10^9/L": "1", "K/uL": "1", "10^3/uL": "1"},
    ),
    _definition(
        "basophils_absolute",
        "Absolute basophil count",
        ("absolute basophils", "basophils absolute", "absolute basophil count"),
        "10^9/L",
        {"10^9/L": "1", "x10^9/L": "1", "K/uL": "1", "10^3/uL": "1"},
    ),
    _definition(
        "neutrophils_percent",
        "Neutrophils",
        ("neutrophils", "neutrophil percent", "neutrophils percent", "neutrophils %"),
        "%",
        {"%": "1"},
    ),
    _definition(
        "lymphocytes_percent",
        "Lymphocytes",
        ("lymphocytes", "lymphocyte percent", "lymphocytes percent", "lymphocytes %"),
        "%",
        {"%": "1"},
    ),
    _definition(
        "monocytes_percent",
        "Monocytes",
        ("monocytes", "monocyte percent", "monocytes percent", "monocytes %"),
        "%",
        {"%": "1"},
    ),
    _definition(
        "eosinophils_percent",
        "Eosinophils",
        ("eosinophils", "eosinophil percent", "eosinophils percent", "eosinophils %"),
        "%",
        {"%": "1"},
    ),
    _definition(
        "basophils_percent",
        "Basophils",
        ("basophils", "basophil percent", "basophils percent", "basophils %"),
        "%",
        {"%": "1"},
    ),
    _definition("sodium", "Sodium", ("sodium", "na"), "mmol/L", {"mmol/L": "1", "mEq/L": "1"}),
    _definition(
        "potassium", "Potassium", ("potassium", "k"), "mmol/L", {"mmol/L": "1", "mEq/L": "1"}
    ),
    _definition(
        "chloride", "Chloride", ("chloride", "cl"), "mmol/L", {"mmol/L": "1", "mEq/L": "1"}
    ),
    _definition(
        "bicarbonate",
        "Bicarbonate / total CO2",
        ("bicarbonate", "total co2", "co2", "carbon dioxide", "carbon dioxide total"),
        "mmol/L",
        {"mmol/L": "1", "mEq/L": "1"},
    ),
    _definition(
        "bun",
        "Blood urea nitrogen",
        ("bun", "blood urea nitrogen", "urea nitrogen"),
        "mg/dL",
        {"mg/dL": "1"},
    ),
    _definition("creatinine", "Creatinine", ("creatinine", "creat"), "mg/dL", {"mg/dL": "1"}),
    _definition("glucose", "Glucose", ("glucose", "blood glucose"), "mg/dL", {"mg/dL": "1"}),
    _definition(
        "calcium",
        "Calcium",
        ("calcium", "total calcium", "calcium total"),
        "mmol/L",
        {"mmol/L": "1", "mg/dL": "0.25"},
    ),
    _definition("magnesium", "Magnesium", ("magnesium", "mg"), "mg/dL", {"mg/dL": "1"}),
    _definition(
        "cortisol",
        "Cortisol",
        ("cortisol", "serum cortisol", "plasma cortisol", "cortisol am", "cortisol pm"),
        "nmol/L",
        {"nmol/L": "1", "mcg/dL": "27.6", "ug/dL": "27.6"},
    ),
)


def _key(value: str) -> str:
    value = value.strip().casefold().replace("μ", "u").replace("µ", "u")
    return re.sub(r"[^a-z0-9%]+", " ", value).strip()


_BY_ALIAS: Final = {
    _key(alias): definition for definition in ANALYTES for alias in definition.aliases
}
_BY_CODE: Final = {definition.code: definition for definition in ANALYTES}


def analyte_definition(code: str | None) -> AnalyteDefinition | None:
    return None if code is None else _BY_CODE.get(code)


def normalize_lab_value(
    analyte_name: str,
    original_value: str | None,
    original_unit: str | None,
) -> NormalizedLabValue | None:
    definition = _BY_ALIAS.get(_key(analyte_name))
    if definition is None:
        return None
    without_value = NormalizedLabValue(definition.code, definition.display_name, None, None, None)
    if original_value is None or original_unit is None:
        return without_value
    try:
        source_value = Decimal(original_value.strip().replace(",", ""))
    except InvalidOperation:
        return without_value
    if not source_value.is_finite():
        return without_value
    factor = next(
        (
            candidate
            for unit, candidate in definition.unit_factors
            if _key(unit) == _key(original_unit)
        ),
        None,
    )
    if factor is None:
        return without_value
    normalized = source_value * factor
    if abs(normalized) > _MAX_ABS:
        return without_value
    normalized = normalized.quantize(_SCALE, rounding=ROUND_HALF_EVEN)
    method = f"{NORMALIZATION_VERSION}:{definition.code}:{factor}"
    return NormalizedLabValue(
        definition.code,
        definition.display_name,
        normalized,
        definition.canonical_unit,
        method,
    )
