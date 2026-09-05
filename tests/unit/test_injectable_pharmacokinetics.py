from __future__ import annotations

import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest

from healthcurve.analytics import injectable_pharmacokinetics, wake_pharmacokinetics
from healthcurve.analytics.exposure import ExposureDose
from healthcurve.analytics.wake_reference import free_from_total
from healthcurve.medications.models import DoseCategory, DoseUnit, Route


def dose(
    *,
    identity: int,
    occurred_at: datetime,
    amount: str = "50",
    route: Route = Route.INTRAVENOUS,
    category: DoseCategory = DoseCategory.STRESS,
    name: str = "Hydrocortisone Inj Dose",
    normalized_name: str = "hydrocortisone inj dose",
    formulation: str = "intravenous push",
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


def test_exact_50_mg_iv_push_has_immediate_literature_fitted_contribution() -> None:
    administered = datetime(2026, 8, 20, 2, tzinfo=UTC)
    curve = cast(
        dict[str, Any],
        injectable_pharmacokinetics.build_curve(
            day=date(2026, 8, 20),
            timezone="UTC",
            doses=[dose(identity=1, occurred_at=administered)],
        ),
    )

    sample = sample_at(curve, administered)
    assert float(sample["derived_total_cortisol_nmol_l_display"]) == pytest.approx(1347.0)
    assert float(sample["modeled_free_cortisol_nmol_l"]) == pytest.approx(
        free_from_total(1347.0), abs=1e-6
    )
    assert curve["supported_dose_count"] == 1
    assert curve["dose_markers"][0]["modeled_peak_at"] == administered


def test_exact_100_mg_iv_push_uses_disclosed_two_times_reference_contribution() -> None:
    administered = datetime(2026, 8, 20, 2, tzinfo=UTC)
    curve = cast(
        dict[str, Any],
        injectable_pharmacokinetics.build_curve(
            day=date(2026, 8, 20),
            timezone="UTC",
            doses=[dose(identity=11, occurred_at=administered, amount="100")],
        ),
    )

    sample = sample_at(curve, administered)
    assert float(sample["derived_total_cortisol_nmol_l_display"]) == pytest.approx(2694.0)
    assert float(sample["modeled_free_cortisol_nmol_l"]) == pytest.approx(
        free_from_total(2694.0), abs=1e-6
    )
    assert curve["supported_dose_count"] == 1
    assert curve["model"]["iv_push_supported_amounts_mg"] == [Decimal("50"), Decimal("100")]


def test_100_mg_iv_push_total_contribution_halves_at_fitted_half_life() -> None:
    administered = datetime(2026, 8, 20, 2, tzinfo=UTC)
    instant = administered + timedelta(
        hours=injectable_pharmacokinetics.IV_ELIMINATION_HALF_LIFE_HOURS
    )

    contribution = injectable_pharmacokinetics.iv_total_contribution(
        dose(identity=12, occurred_at=administered, amount="100"), instant
    )

    assert contribution == pytest.approx(2694.0 / 2, abs=1e-7)


def test_iv_push_total_contribution_halves_at_fitted_half_life() -> None:
    administered = datetime(2026, 8, 20, 2, tzinfo=UTC)
    instant = administered + timedelta(
        hours=injectable_pharmacokinetics.IV_ELIMINATION_HALF_LIFE_HOURS
    )

    contribution = injectable_pharmacokinetics.iv_total_contribution(
        dose(identity=2, occurred_at=administered), instant
    )

    assert contribution == pytest.approx(1347.0 / 2, abs=1e-7)


def test_repeated_six_hour_iv_pushes_add_without_a_plan_or_reset() -> None:
    first_at = datetime(2026, 8, 20, 2, tzinfo=UTC)
    second_at = first_at + timedelta(hours=6)
    first = dose(identity=3, occurred_at=first_at)
    second = dose(identity=4, occurred_at=second_at)
    curve = cast(
        dict[str, Any],
        injectable_pharmacokinetics.build_curve(
            day=date(2026, 8, 20), timezone="UTC", doses=[first, second]
        ),
    )

    sample = sample_at(curve, second_at)
    expected_total = 1347.0 + 1347.0 * math.exp(-0.27 * 6)
    assert float(sample["derived_total_cortisol_nmol_l_display"]) == pytest.approx(
        expected_total, abs=1e-6
    )
    assert curve["supported_dose_count"] == 2


def test_overlapping_50_and_100_mg_iv_pushes_add_without_reset() -> None:
    first_at = datetime(2026, 8, 20, 2, tzinfo=UTC)
    second_at = first_at + timedelta(hours=1)
    curve = cast(
        dict[str, Any],
        injectable_pharmacokinetics.build_curve(
            day=date(2026, 8, 20),
            timezone="UTC",
            doses=[
                dose(identity=14, occurred_at=first_at, amount="50"),
                dose(identity=15, occurred_at=second_at, amount="100"),
            ],
        ),
    )

    sample = sample_at(curve, second_at)
    expected_total = 2694.0 + 1347.0 * math.exp(-0.27)
    assert float(sample["derived_total_cortisol_nmol_l_display"]) == pytest.approx(
        expected_total, abs=1e-6
    )
    assert curve["supported_dose_count"] == 2


def test_regular_and_stress_iv_contributions_remain_separate_and_sum() -> None:
    administered = datetime(2026, 8, 20, 2, tzinfo=UTC)
    curve = cast(
        dict[str, Any],
        injectable_pharmacokinetics.build_curve(
            day=date(2026, 8, 20),
            timezone="UTC",
            doses=[
                dose(identity=5, occurred_at=administered, category=DoseCategory.SCHEDULED),
                dose(identity=6, occurred_at=administered, category=DoseCategory.STRESS),
            ],
        ),
    )

    sample = sample_at(curve, administered)
    assert sample["regular_modeled_free_cortisol_nmol_l"] > 0
    assert sample["stress_modeled_free_cortisol_nmol_l"] > 0
    assert sample["modeled_free_cortisol_nmol_l"] == (
        sample["regular_modeled_free_cortisol_nmol_l"]
        + sample["stress_modeled_free_cortisol_nmol_l"]
    )


@pytest.mark.parametrize(
    ("recorded", "reason"),
    [
        (
            dose(
                identity=7, occurred_at=datetime(2026, 8, 20, tzinfo=UTC), route=Route.INTRAMUSCULAR
            ),
            "unsupported_route",
        ),
        (
            dose(identity=8, occurred_at=datetime(2026, 8, 20, tzinfo=UTC), amount="40"),
            "unsupported_amount",
        ),
        (
            dose(identity=13, occurred_at=datetime(2026, 8, 20, tzinfo=UTC), amount="150"),
            "unsupported_amount",
        ),
        (
            dose(
                identity=9, occurred_at=datetime(2026, 8, 20, tzinfo=UTC), formulation="injection"
            ),
            None,
        ),
    ],
)
def test_route_amount_and_formulation_support_is_explicit(
    recorded: ExposureDose, reason: str | None
) -> None:
    assert injectable_pharmacokinetics.injectable_exclusion_reason(recorded) == reason


def test_oral_only_output_is_unchanged_between_v3_and_v4() -> None:
    administered = datetime(2026, 8, 20, 7, tzinfo=UTC)
    oral = dose(
        identity=10,
        occurred_at=administered,
        amount="10",
        route=Route.ORAL,
        category=DoseCategory.SCHEDULED,
        name="Hydrocortisone",
        normalized_name="hydrocortisone",
        formulation="tablet",
    )
    v3 = cast(
        dict[str, Any],
        wake_pharmacokinetics.build_curve(day=date(2026, 8, 20), timezone="UTC", doses=[oral]),
    )
    v4 = cast(
        dict[str, Any],
        injectable_pharmacokinetics.build_curve(
            day=date(2026, 8, 20), timezone="UTC", doses=[oral]
        ),
    )

    v3_values = {
        sample["occurred_at"]: float(sample["modeled_free_cortisol_nmol_l"])
        for sample in v3["samples"]
    }
    v4_values = {
        sample["occurred_at"]: float(sample["modeled_free_cortisol_nmol_l"])
        for sample in v4["samples"]
    }
    assert v4_values == pytest.approx(v3_values, abs=1e-6)
    assert v3["model"]["id"] == "hc-wake-free-v3"
    assert v4["model"]["id"] == "hc-mixed-route-free-v4"


def test_owner_curve_uses_only_corrected_dose_leaf(monkeypatch: pytest.MonkeyPatch) -> None:
    owner_id = uuid.UUID(int=101)
    original_id = uuid.UUID(int=201)
    correction_id = uuid.UUID(int=202)

    def row(identity: uuid.UUID, occurred_at: datetime, supersedes_id: uuid.UUID | None) -> Any:
        return SimpleNamespace(
            id=identity,
            owner_id=owner_id,
            occurred_at=occurred_at,
            local_time=occurred_at.replace(tzinfo=None),
            timezone="UTC",
            utc_offset_minutes=0,
            amount=Decimal("15"),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            category=DoseCategory.SCHEDULED,
            medication=SimpleNamespace(
                name="Hydrocortisone", normalized_name="hydrocortisone", formulation="tablet"
            ),
            source_type=SimpleNamespace(value="telegram"),
            confirmation_state=SimpleNamespace(value="confirmed_from_draft"),
            supersedes_id=supersedes_id,
            voided=False,
            recorded_at=occurred_at + timedelta(minutes=30),
            source_revision=None,
        )

    original = row(original_id, datetime(2026, 9, 5, 7, 30, tzinfo=UTC), None)
    correction = row(
        correction_id,
        datetime(2026, 9, 5, 7, tzinfo=UTC),
        original_id,
    )

    class FakeSession:
        calls = 0

        def scalars(self, _statement: object) -> list[Any]:
            self.calls += 1
            return [original, correction] if self.calls == 1 else [original_id]

    def active_parameters(_session: object, *, owner_id: uuid.UUID) -> Any:
        assert owner_id == uuid.UUID(int=101)
        return wake_pharmacokinetics.DEFAULT_PARAMETERS

    monkeypatch.setattr(
        injectable_pharmacokinetics.oral_model,
        "active_parameters",
        active_parameters,
    )
    curve = cast(
        dict[str, Any],
        injectable_pharmacokinetics.curve_for_owner(
            cast(Any, FakeSession()),
            owner_id=owner_id,
            day=date(2026, 9, 5),
            timezone="UTC",
        ),
    )

    assert curve["supported_dose_count"] == 1
    assert [marker["dose_event_id"] for marker in curve["dose_markers"]] == [correction_id]
    expected = injectable_pharmacokinetics.build_curve(
        day=date(2026, 9, 5),
        timezone="UTC",
        parameters=wake_pharmacokinetics.DEFAULT_PARAMETERS,
        doses=[
            dose(
                identity=202,
                occurred_at=datetime(2026, 9, 5, 7, tzinfo=UTC),
                amount="15",
                route=Route.ORAL,
                category=DoseCategory.SCHEDULED,
                name="Hydrocortisone",
                normalized_name="hydrocortisone",
                formulation="tablet",
            )
        ],
    )
    assert curve["samples"] == expected["samples"]
