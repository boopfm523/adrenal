from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest

from healthcurve.analytics import full_glucocorticoid_pharmacokinetics as v5
from healthcurve.analytics import wake_pharmacokinetics
from healthcurve.analytics.exposure import ExposureDose
from healthcurve.medications.models import DoseCategory, DoseUnit, Route


def dose(
    *,
    identity: int,
    occurred_at: datetime,
    amount: str = "5",
    route: Route = Route.ORAL,
    category: DoseCategory = DoseCategory.SCHEDULED,
    name: str = "Prednisone",
    normalized_name: str = "prednisone",
    formulation: str = "tablet",
) -> ExposureDose:
    return ExposureDose(
        id=uuid.UUID(int=identity),
        occurred_at=occurred_at,
        local_time=occurred_at.replace(tzinfo=None),
        timezone="UTC",
        utc_offset_minutes=0,
        amount=Decimal(amount),
        unit=DoseUnit.MG,
        route=route,
        category=category,
        medication_name=name,
        normalized_medication_name=normalized_name,
        formulation=formulation,
        source_type="web",
        confirmation_state="direct",
        supersedes_id=None,
        recorded_at=occurred_at + timedelta(seconds=1),
        source_revision=None,
    )


def sample_at(curve: dict[str, Any], instant: datetime) -> dict[str, Any]:
    return next(sample for sample in curve["samples"] if sample["occurred_at"] == instant)


def test_isolated_prednisone_dose_contributes_nothing_before_administration() -> None:
    administered = datetime(2026, 11, 15, 7, tzinfo=UTC)
    curve = cast(
        dict[str, Any],
        v5.build_curve(
            day=date(2026, 11, 15),
            timezone="UTC",
            doses=[dose(identity=1, occurred_at=administered)],
        ),
    )
    sample = sample_at(curve, administered)
    assert sample["modeled_free_cortisol_nmol_l"] == Decimal("0E-9")
    assert sample["prednisone_modeled_free_cortisol_equivalent_nmol_l"] == Decimal("0E-9")


def test_isolated_prednisone_dose_peaks_at_its_own_published_timing() -> None:
    administered = datetime(2026, 11, 15, 7, tzinfo=UTC)
    curve = cast(
        dict[str, Any],
        v5.build_curve(
            day=date(2026, 11, 15),
            timezone="UTC",
            doses=[dose(identity=2, occurred_at=administered, amount="5")],
        ),
    )
    peak_at = administered + timedelta(hours=v5.PREDNISOLONE_PEAK_TIME_HOURS)
    sample = sample_at(curve, peak_at)
    equivalent_hc_mg = 5.0 * v5.PREDNISONE_HYDROCORTISONE_EQUIVALENCE_MG_PER_MG
    expected = wake_pharmacokinetics.DEFAULT_PARAMETERS.free_peak_10_mg_nmol_l * (
        equivalent_hc_mg / 10.0
    )
    assert float(sample["prednisone_modeled_free_cortisol_equivalent_nmol_l"]) == pytest.approx(
        expected, abs=1e-6
    )
    assert float(sample["hydrocortisone_modeled_free_cortisol_nmol_l"]) == 0.0
    assert curve["dose_markers"][0]["modeled_peak_at"] == peak_at


def test_hydrocortisone_and_prednisone_contributions_sum_and_stay_decomposed() -> None:
    wake_at = datetime(2026, 11, 15, 7, tzinfo=UTC)
    afternoon_at = datetime(2026, 11, 15, 16, tzinfo=UTC)
    doses = [
        dose(identity=3, occurred_at=wake_at, amount="5"),
        dose(
            identity=4,
            occurred_at=afternoon_at,
            amount="10",
            name="Hydrocortisone",
            normalized_name="hydrocortisone",
        ),
    ]
    curve = cast(
        dict[str, Any],
        v5.build_curve(day=date(2026, 11, 15), timezone="UTC", doses=doses),
    )
    assert curve["supported_dose_count"] == 2
    for sample in curve["samples"]:
        total = sample["modeled_free_cortisol_nmol_l"]
        hc = sample["hydrocortisone_modeled_free_cortisol_nmol_l"]
        prednisone = sample["prednisone_modeled_free_cortisol_equivalent_nmol_l"]
        assert total == hc + prednisone
        regular = sample["regular_modeled_free_cortisol_nmol_l"]
        stress = sample["stress_modeled_free_cortisol_nmol_l"]
        assert hc == regular + stress

    hc_peak_marker = next(
        marker for marker in curve["dose_markers"] if marker["medication_name"] == "Hydrocortisone"
    )
    at_hc_peak = sample_at(curve, cast(datetime, hc_peak_marker["modeled_peak_at"]))
    assert float(at_hc_peak["hydrocortisone_modeled_free_cortisol_nmol_l"]) > 0.0
    assert float(at_hc_peak["prednisone_modeled_free_cortisol_equivalent_nmol_l"]) > 0.0


def test_oral_hydrocortisone_only_output_is_unchanged_from_v4() -> None:
    administered = datetime(2026, 11, 15, 7, tzinfo=UTC)
    hc_dose = dose(
        identity=5,
        occurred_at=administered,
        amount="10",
        name="Hydrocortisone",
        normalized_name="hydrocortisone",
    )
    from healthcurve.analytics import injectable_pharmacokinetics

    v4_curve = cast(
        dict[str, Any],
        injectable_pharmacokinetics.build_curve(
            day=date(2026, 11, 15), timezone="UTC", doses=[hc_dose]
        ),
    )
    v5_curve = cast(
        dict[str, Any],
        v5.build_curve(day=date(2026, 11, 15), timezone="UTC", doses=[hc_dose]),
    )
    v4_values = {
        sample["occurred_at"]: float(sample["modeled_free_cortisol_nmol_l"])
        for sample in v4_curve["samples"]
    }
    v5_values = {
        sample["occurred_at"]: float(sample["hydrocortisone_modeled_free_cortisol_nmol_l"])
        for sample in v5_curve["samples"]
        if sample["occurred_at"] in v4_values
    }
    assert v5_values == pytest.approx(v4_values, abs=1e-6)


@pytest.mark.parametrize(
    ("recorded", "reason"),
    [
        (
            dose(
                identity=6,
                occurred_at=datetime(2026, 11, 15, tzinfo=UTC),
                name="Dexamethasone",
                normalized_name="dexamethasone",
            ),
            "unsupported_medication",
        ),
        (
            dose(
                identity=7,
                occurred_at=datetime(2026, 11, 15, tzinfo=UTC),
                formulation="delayed release",
            ),
            "unsupported_formulation",
        ),
        (
            dose(
                identity=8, occurred_at=datetime(2026, 11, 15, tzinfo=UTC), route=Route.INTRAVENOUS
            ),
            "unsupported_route",
        ),
        (dose(identity=9, occurred_at=datetime(2026, 11, 15, tzinfo=UTC)), None),
    ],
)
def test_prednisone_support_boundary_is_explicit(
    recorded: ExposureDose, reason: str | None
) -> None:
    assert v5.prednisone_exclusion_reason(recorded) == reason
    assert v5.exclusion_reason(recorded) == reason


def test_prednisolone_shape_matches_its_own_published_timing_not_hydrocortisones() -> None:
    assert v5.PREDNISOLONE_ELIMINATION_HALF_LIFE_HOURS != pytest.approx(
        wake_pharmacokinetics.DEFAULT_PARAMETERS.elimination_half_life_hours
    )
    administered = datetime(2026, 11, 15, 7, tzinfo=UTC)
    prednisone_dose = dose(identity=11, occurred_at=administered, amount="5")

    def prednisone_contribution_at(elapsed_hours: float) -> float:
        return v5.prednisone_contribution_nmol_per_liter(
            prednisone_dose, administered + timedelta(hours=elapsed_hours)
        )

    peak_contribution = prednisone_contribution_at(v5.PREDNISOLONE_PEAK_TIME_HOURS)
    assert peak_contribution > 0.0

    # Far past the peak, absorption is exhausted and decay is governed purely by the
    # prednisolone elimination rate, so successive elimination half-lives should each
    # roughly halve the remaining contribution.
    far_elapsed = v5.PREDNISOLONE_PEAK_TIME_HOURS + 10 * v5.PREDNISOLONE_ELIMINATION_HALF_LIFE_HOURS
    far_contribution = prednisone_contribution_at(far_elapsed)
    one_half_life_later = prednisone_contribution_at(
        far_elapsed + v5.PREDNISOLONE_ELIMINATION_HALF_LIFE_HOURS
    )
    assert one_half_life_later == pytest.approx(far_contribution / 2.0, rel=1e-3)

    # Compare each drug's own normalized decay (contribution relative to its own peak) at
    # the same elapsed time. Contribution is linear in dose amount for a fixed shape, so
    # this ratio is amount-independent. If v5 reused hydrocortisone's shape for prednisone,
    # the two ratios would match; they do not, because prednisolone's own absorption and
    # elimination timing differs from hydrocortisone's.
    hc_peak = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=Decimal("10"),
        elapsed_hours=wake_pharmacokinetics.DEFAULT_PARAMETERS.peak_time_hours,
    )
    hc_at_5h = wake_pharmacokinetics.concentration_nmol_per_liter(
        amount_mg=Decimal("10"), elapsed_hours=5.0
    )
    prednisone_at_5h = prednisone_contribution_at(5.0)
    assert (prednisone_at_5h / peak_contribution) != pytest.approx(hc_at_5h / hc_peak, rel=0.05)


def test_prednisone_contribution_is_zero_outside_its_horizon() -> None:
    administered = datetime(2026, 11, 15, 7, tzinfo=UTC)
    far_future = administered + timedelta(hours=v5.PREDNISONE_HORIZON_HOURS + 1)
    contribution = v5.prednisone_contribution_nmol_per_liter(
        dose(identity=10, occurred_at=administered), far_future
    )
    assert contribution == 0.0


def test_model_metadata_discloses_prednisone_parameters() -> None:
    curve = cast(
        dict[str, Any],
        v5.build_curve(day=date(2026, 11, 15), timezone="UTC", doses=[]),
    )
    model = curve["model"]
    assert model["id"] == "hc-full-glucocorticoid-v5"
    assert "prednisone" in model["supported_medications"]
    assert model["prednisone_hydrocortisone_equivalence_mg_per_mg"] == Decimal("4.0")
    assert math.isfinite(float(model["prednisolone_elimination_half_life_hours"]))
