from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from healthcurve.analytics import exposure, physiology, wake_pharmacokinetics
from healthcurve.analytics.exposure import ExposureDose
from healthcurve.analytics.wake_reference import total_from_free
from healthcurve.medications.models import DoseCategory, DoseUnit, Route


def dose(
    *,
    identity: int,
    occurred_at: datetime,
    amount: str = "10",
    category: DoseCategory = DoseCategory.SCHEDULED,
) -> ExposureDose:
    local = occurred_at.astimezone(UTC)
    return ExposureDose(
        id=uuid.UUID(int=identity),
        occurred_at=occurred_at,
        local_time=local.replace(tzinfo=None),
        timezone="UTC",
        utc_offset_minutes=0,
        amount=Decimal(amount),
        unit=DoseUnit.MG,
        route=Route.ORAL,
        category=category,
        medication_name="Hydrocortisone",
        normalized_medication_name="hydrocortisone",
        formulation="tablet",
        source_type="web",
        confirmation_state="direct",
        supersedes_id=None,
        recorded_at=occurred_at + timedelta(seconds=1),
        source_revision=None,
    )


def test_reviewed_population_defaults_and_calibration_are_explicit() -> None:
    parameters = wake_pharmacokinetics.DEFAULT_PARAMETERS

    assert parameters.elimination_half_life_hours == 1.6
    assert parameters.peak_time_hours == 1.1
    assert parameters.distribution_volume_liters == 38.7
    assert parameters.oral_bioavailability == 0.95
    assert wake_pharmacokinetics.REFERENCE_ABSORPTION_DURATION_HOURS == 0.54
    assert wake_pharmacokinetics.REFERENCE_CLEARANCE_LITERS_PER_HOUR == 12.1
    assert parameters.free_peak_10_mg_nmol_l == pytest.approx(69.5992528694, abs=1e-9)
    assert total_from_free(parameters.free_peak_10_mg_nmol_l) / total_from_free(
        wake_pharmacokinetics.DEFAULT_HEALTHY_FREE_ACROPHASE_NMOL_L
    ) == pytest.approx(1.3, abs=0.04)


def test_oral_absorption_delays_peak_until_configured_tmax() -> None:
    parameters = wake_pharmacokinetics.DEFAULT_PARAMETERS
    at_dose = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=Decimal("10"), elapsed_hours=0
    )
    before_peak = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=Decimal("10"), elapsed_hours=0.5
    )
    at_peak = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=Decimal("10"), elapsed_hours=parameters.peak_time_hours
    )
    after_peak = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=Decimal("10"), elapsed_hours=2
    )

    assert at_dose == 0
    assert 0 < before_peak < at_peak
    assert after_peak < at_peak
    assert at_peak == pytest.approx(parameters.free_peak_10_mg_nmol_l, abs=1e-9)


@given(
    st.decimals(min_value="0", max_value="100", places=4, allow_nan=False),
    st.floats(min_value=-1, max_value=72, allow_nan=False, allow_infinity=False),
)
def test_supported_domain_is_finite_and_nonnegative(amount: Decimal, elapsed: float) -> None:
    result = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=amount,
        elapsed_hours=elapsed,
    )
    assert math.isfinite(result)
    assert result >= 0


def test_nearby_regular_and_stress_doses_sum_without_deduplication() -> None:
    administered = datetime(2026, 8, 11, 7, tzinfo=UTC)
    regular = dose(identity=1, occurred_at=administered, amount="10")
    stress = dose(
        identity=2,
        occurred_at=administered + timedelta(minutes=1),
        amount="5",
        category=DoseCategory.STRESS,
    )
    curve = cast(
        dict[str, Any],
        wake_pharmacokinetics.build_curve(
            day=date(2026, 8, 11),
            timezone="UTC",
            doses=[stress, regular],
        ),
    )

    assert curve["supported_dose_count"] == 2
    assert [marker["dose_event_id"] for marker in curve["dose_markers"]] == [
        regular.id,
        stress.id,
    ]
    for sample in curve["samples"]:
        assert sample["modeled_free_cortisol_nmol_l"] == (
            sample["regular_modeled_free_cortisol_nmol_l"]
            + sample["stress_modeled_free_cortisol_nmol_l"]
        )
    assert max(sample["regular_modeled_free_cortisol_nmol_l"] for sample in curve["samples"]) > 0
    assert max(sample["stress_modeled_free_cortisol_nmol_l"] for sample in curve["samples"]) > 0


def test_free_is_dose_proportional_while_display_total_is_nonlinear() -> None:
    peak_at = wake_pharmacokinetics.DEFAULT_PARAMETERS.peak_time_hours
    free_5 = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=Decimal("5"), elapsed_hours=peak_at
    )
    free_10 = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=Decimal("10"), elapsed_hours=peak_at
    )

    assert free_10 == pytest.approx(2 * free_5, abs=1e-10)
    assert total_from_free(free_10) != pytest.approx(2 * total_from_free(free_5), abs=1e-6)


def test_owner_parameters_change_timing_amplitude_and_revision_fingerprint() -> None:
    administered = datetime(2026, 8, 11, 7, tzinfo=UTC)
    recorded = dose(identity=8, occurred_at=administered)
    custom = wake_pharmacokinetics.WakeFreeParameters(
        elimination_half_life_hours=2.1,
        peak_time_hours=1.5,
        distribution_volume_liters=45,
        oral_bioavailability=0.8,
        revision_id=uuid.UUID(int=99),
        revision_number=3,
    )
    default_curve = cast(
        dict[str, Any],
        wake_pharmacokinetics.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[recorded]),
    )
    custom_curve = cast(
        dict[str, Any],
        wake_pharmacokinetics.build_curve(
            day=date(2026, 8, 11),
            timezone="UTC",
            doses=[recorded],
            parameters=custom,
        ),
    )

    assert default_curve["source_revision_sha256"] != custom_curve["source_revision_sha256"]
    assert default_curve["model"]["parameters"]["population_default"] is True
    assert custom_curve["model"]["parameters"]["revision_number"] == 3
    assert custom_curve["dose_markers"][0]["modeled_peak_at"] == administered + timedelta(hours=1.5)
    assert max(sample["modeled_free_cortisol_nmol_l"] for sample in custom_curve["samples"]) < max(
        sample["modeled_free_cortisol_nmol_l"] for sample in default_curve["samples"]
    )


def test_v3_is_additive_and_does_not_change_existing_model_outputs() -> None:
    recorded = dose(identity=9, occurred_at=datetime(2026, 8, 11, 7, tzinfo=UTC))
    v1_before = exposure.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[recorded])
    v2_before = physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[recorded])

    v3 = wake_pharmacokinetics.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[recorded])
    v3_model = cast(dict[str, Any], v3["model"])

    assert v3_model["id"] == "hc-wake-free-v3"
    assert v3["series_unit"] == "nmol/L"
    assert (
        exposure.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[recorded]) == v1_before
    )
    assert (
        physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[recorded]) == v2_before
    )


@pytest.mark.parametrize(
    "parameters",
    [
        wake_pharmacokinetics.WakeFreeParameters(elimination_half_life_hours=0),
        wake_pharmacokinetics.WakeFreeParameters(peak_time_hours=9),
        wake_pharmacokinetics.WakeFreeParameters(distribution_volume_liters=-1),
        wake_pharmacokinetics.WakeFreeParameters(oral_bioavailability=1.1),
    ],
)
def test_invalid_parameters_fail_closed(
    parameters: wake_pharmacokinetics.WakeFreeParameters,
) -> None:
    with pytest.raises(ValueError, match="parameter"):
        wake_pharmacokinetics.concentration_nmol_per_liter(
            amount_mg=Decimal("10"), elapsed_hours=1, parameters=parameters
        )
