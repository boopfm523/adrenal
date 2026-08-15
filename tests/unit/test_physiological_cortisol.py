import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from healthcurve.analytics import exposure, physiology
from healthcurve.analytics.exposure import ExposureDose
from healthcurve.medications.models import DoseCategory, DoseUnit, Route


def dose(
    *,
    identity: int,
    occurred_at: datetime,
    amount: str = "10",
    medication: str = "hydrocortisone",
    formulation: str | None = "tablet",
    route: Route = Route.ORAL,
    unit: DoseUnit = DoseUnit.MG,
    category: DoseCategory = DoseCategory.SCHEDULED,
    supersedes_id: uuid.UUID | None = None,
    source_revision: str | None = None,
) -> ExposureDose:
    local = occurred_at.astimezone(UTC)
    return ExposureDose(
        id=uuid.UUID(int=identity),
        occurred_at=occurred_at,
        local_time=local.replace(tzinfo=None),
        timezone="UTC",
        utc_offset_minutes=0,
        amount=Decimal(amount),
        unit=unit,
        route=route,
        category=category,
        medication_name=medication.title(),
        normalized_medication_name=medication,
        formulation=formulation,
        source_type="web",
        confirmation_state="direct",
        supersedes_id=supersedes_id,
        recorded_at=occurred_at + timedelta(seconds=1),
        source_revision=source_revision,
    )


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (-1 / 60, 0.0),
        (0.0, 0.0),
        (0.25, 1.544866648),
        (0.5, 2.453002599),
        (1.0, 3.131366639),
        (2.0, 2.677108979),
        (4.0, 1.153520516),
        (8.0, 0.162244712),
        (12.0, 0.022235822),
        (24.0, 0.000057120),
        (49.0, 0.0),
    ],
)
def test_v2_direct_free_model_matches_adr_0024_gold_values(elapsed: float, expected: float) -> None:
    assert physiology.concentration_nmol_per_liter(
        amount_mg=Decimal("1"), elapsed_hours=elapsed
    ) == pytest.approx(expected, abs=1e-9)


def test_v2_peak_and_population_disposition_match_adr_0024() -> None:
    parameters = physiology.DEFAULT_PARAMETERS

    assert parameters.elimination_rate_per_hour == pytest.approx(0.4970276993, abs=1e-10)
    assert parameters.elimination_half_life_hours == pytest.approx(1.394584611, abs=1e-9)
    assert parameters.peak_time_hours == pytest.approx(1.146858832, abs=1e-9)
    assert physiology.concentration_nmol_per_liter(
        amount_mg=Decimal("10"), elapsed_hours=parameters.peak_time_hours
    ) == pytest.approx(31.573881313, abs=1e-9)


def test_near_equal_absorption_and_elimination_uses_finite_limit() -> None:
    parameters = physiology.PhysiologyParameters(
        absorption_rate_per_hour=1.0,
        clearance_liters_per_hour=100.0,
        distribution_volume_liters=100.0,
    )

    result = physiology.concentration_nmol_per_liter(
        amount_mg=Decimal("10"), elapsed_hours=1.0, parameters=parameters
    )
    scale = 0.96 * 10 / 100 * (1_000_000 / 362.46)

    assert parameters.peak_time_hours == pytest.approx(1.0)
    assert result == pytest.approx(scale * math.exp(-1.0), abs=1e-12)
    assert math.isfinite(result)
    assert result >= 0


@given(
    st.decimals(min_value="0", max_value="100", places=4, allow_nan=False),
    st.floats(min_value=-1, max_value=48, allow_nan=False, allow_infinity=False),
)
def test_v2_supported_domain_is_finite_and_nonnegative(amount: Decimal, elapsed: float) -> None:
    result = physiology.concentration_nmol_per_liter(amount_mg=amount, elapsed_hours=elapsed)
    assert math.isfinite(result)
    assert result >= 0


def test_close_and_simultaneous_doses_sum_without_deduplication() -> None:
    first_at = datetime(2026, 8, 11, 7, tzinfo=UTC)
    first = dose(identity=1, occurred_at=first_at, amount="10")
    close = dose(identity=2, occurred_at=first_at + timedelta(minutes=1), amount="5")
    same = dose(identity=3, occurred_at=first_at, amount="5")
    instant = first_at + timedelta(hours=physiology.DEFAULT_PARAMETERS.peak_time_hours)

    close_curve = cast(
        dict[str, Any],
        physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[close, first]),
    )
    same_curve = cast(
        dict[str, Any],
        physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[same, first]),
    )
    close_sample = next(row for row in close_curve["samples"] if row["occurred_at"] == instant)
    same_sample = next(row for row in same_curve["samples"] if row["occurred_at"] == instant)

    assert close_curve["supported_dose_count"] == 2
    assert close_sample["modeled_free_cortisol_nmol_l"] == physiology.modeled_free_cortisol_at(
        [first, close], instant
    )
    assert same_sample["modeled_free_cortisol_nmol_l"] == physiology.modeled_free_cortisol_at(
        [first, same], instant
    )
    assert [row["dose_event_id"] for row in same_curve["dose_markers"]] == [first.id, same.id]


def test_samples_split_stress_and_regular_contributions_once() -> None:
    administered = datetime(2026, 8, 10, 23, 30, tzinfo=UTC)
    regular = dose(identity=31, occurred_at=administered, amount="10")
    stress = dose(
        identity=32,
        occurred_at=administered + timedelta(minutes=1),
        amount="5",
        category=DoseCategory.STRESS,
    )
    curve = cast(
        dict[str, Any],
        physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[stress, regular]),
    )

    for sample in curve["samples"]:
        assert sample["modeled_free_cortisol_nmol_l"] == (
            sample["regular_modeled_free_cortisol_nmol_l"]
            + sample["stress_modeled_free_cortisol_nmol_l"]
        )
    assert curve["samples"][0]["regular_modeled_free_cortisol_nmol_l"] > 0
    assert curve["samples"][0]["stress_modeled_free_cortisol_nmol_l"] > 0


def test_curve_is_order_independent_and_includes_48_hour_carryover() -> None:
    prior = dose(identity=4, occurred_at=datetime(2026, 8, 10, 1, tzinfo=UTC))
    morning = dose(identity=5, occurred_at=datetime(2026, 8, 11, 7, tzinfo=UTC))

    forward = cast(
        dict[str, Any],
        physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[prior, morning]),
    )
    reverse = cast(
        dict[str, Any],
        physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[morning, prior]),
    )

    assert forward["samples"] == reverse["samples"]
    assert forward["dose_markers"] == reverse["dose_markers"]
    assert forward["source_revision_sha256"] == reverse["source_revision_sha256"]
    assert forward["dose_markers"][0]["carryover"] is True
    assert forward["samples"][0]["modeled_free_cortisol_nmol_l"] > 0


@pytest.mark.parametrize(
    ("kwargs", "reason"),
    [
        ({"medication": "prednisone"}, "unsupported_medication"),
        ({"formulation": None}, "unsupported_formulation"),
        ({"route": Route.INTRAMUSCULAR}, "unsupported_route"),
        ({"unit": DoseUnit.ML}, "unsupported_unit"),
    ],
)
def test_unsupported_doses_remain_markers_with_reasons(kwargs: dict[str, Any], reason: str) -> None:
    item = dose(identity=6, occurred_at=datetime(2026, 8, 11, 7, tzinfo=UTC), **kwargs)
    curve = cast(
        dict[str, Any],
        physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[item]),
    )

    assert curve["supported_dose_count"] == 0
    assert curve["excluded_dose_count"] == 1
    assert curve["dose_markers"][0]["exclusion_reason"] == reason
    assert all(row["modeled_free_cortisol_nmol_l"] == 0 for row in curve["samples"])


@pytest.mark.parametrize(
    ("day", "elapsed_hours"),
    [(date(2026, 3, 8), Decimal("23.0")), (date(2026, 11, 1), Decimal("25.0"))],
)
def test_local_day_uses_elapsed_dst_boundaries(day: date, elapsed_hours: Decimal) -> None:
    curve = cast(
        dict[str, Any], physiology.build_curve(day=day, timezone="America/New_York", doses=[])
    )
    instants = [row["occurred_at"] for row in curve["samples"]]

    assert curve["elapsed_hours"] == elapsed_hours
    assert instants == sorted(set(instants))
    assert "not a measured cortisol" in curve["safety_label"]


def test_fall_back_repeated_hour_has_distinct_offsets() -> None:
    curve = cast(
        dict[str, Any],
        physiology.build_curve(day=date(2026, 11, 1), timezone="America/New_York", doses=[]),
    )
    repeated = [
        (row["local_time"].isoformat(), row["utc_offset_minutes"])
        for row in curve["samples"]
        if row["local_time"].hour == 1 and row["local_time"].minute == 30
    ]

    assert repeated == [
        ("2026-11-01T01:30:00", -240),
        ("2026-11-01T01:30:00", -300),
    ]


def test_source_revision_changes_for_source_or_correction_metadata() -> None:
    occurred_at = datetime(2026, 8, 11, 7, tzinfo=UTC)
    original = dose(identity=40, occurred_at=occurred_at, source_revision="provider-a")
    changed_source = dose(identity=40, occurred_at=occurred_at, source_revision="provider-b")
    correction = dose(
        identity=41,
        occurred_at=occurred_at,
        supersedes_id=original.id,
        source_revision="provider-a",
    )

    original_curve = physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[original])
    source_curve = physiology.build_curve(
        day=date(2026, 8, 11), timezone="UTC", doses=[changed_source]
    )
    correction_curve = physiology.build_curve(
        day=date(2026, 8, 11), timezone="UTC", doses=[correction]
    )

    assert original_curve["source_revision_sha256"] != source_curve["source_revision_sha256"]
    assert original_curve["source_revision_sha256"] != correction_curve["source_revision_sha256"]


def test_owner_curve_uses_only_current_correction_chain_head(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    owner_id = uuid.UUID(int=101)
    occurred_at = datetime(2026, 8, 11, 7, tzinfo=UTC)
    original_id = uuid.UUID(int=60)
    correction_id = uuid.UUID(int=61)

    def row(identity: uuid.UUID, supersedes_id: uuid.UUID | None, amount: str) -> Any:
        return SimpleNamespace(
            id=identity,
            owner_id=owner_id,
            occurred_at=occurred_at,
            local_time=occurred_at.replace(tzinfo=None),
            timezone="UTC",
            utc_offset_minutes=0,
            amount=Decimal(amount),
            unit=DoseUnit.MG,
            route=Route.ORAL,
            category=DoseCategory.SCHEDULED,
            medication=SimpleNamespace(
                name="Hydrocortisone", normalized_name="hydrocortisone", formulation="tablet"
            ),
            source_type=SimpleNamespace(value="web"),
            confirmation_state=SimpleNamespace(value="direct"),
            supersedes_id=supersedes_id,
            recorded_at=occurred_at + timedelta(seconds=1),
            source_revision=None,
        )

    original = row(original_id, None, "10")
    correction = row(correction_id, original_id, "5")

    class FakeSession:
        def scalars(self, _statement: object) -> list[Any]:
            return [original, correction]

    def current_only(_session: object, _model: object, rows: list[Any]) -> list[Any]:
        assert rows == [original, correction]
        return [correction]

    monkeypatch.setattr(physiology.event_service, "current_only", current_only)
    curve = cast(
        dict[str, Any],
        physiology.curve_for_owner(
            cast(Any, FakeSession()), owner_id=owner_id, day=date(2026, 8, 11), timezone="UTC"
        ),
    )

    assert curve["supported_dose_count"] == 1
    assert [marker["dose_event_id"] for marker in curve["dose_markers"]] == [correction_id]
    peak = max(sample["modeled_free_cortisol_nmol_l"] for sample in curve["samples"])
    assert peak == Decimal("15.786940657")


def test_v2_metadata_names_modeled_series_and_v1_output_remains_unchanged() -> None:
    administered = datetime(2026, 8, 11, 7, tzinfo=UTC)
    without_revision = dose(identity=50, occurred_at=administered)
    with_revision = dose(identity=50, occurred_at=administered, source_revision="revision")

    v2 = cast(
        dict[str, Any],
        physiology.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[with_revision]),
    )
    v1_without = exposure.build_curve(
        day=date(2026, 8, 11), timezone="UTC", doses=[without_revision]
    )
    v1_with = exposure.build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[with_revision])

    assert v2["series_kind"] == "modeled_plasma_free_cortisol_scenario"
    assert v2["series_unit"] == "nmol/L"
    assert v2["model"]["id"] == "hc-physiology-v2"
    assert v2["model"]["revision"] == "hc-physiology-v2.0.0"
    assert "measured cortisol" in v2["safety_label"]
    assert v1_without == v1_with


@pytest.mark.parametrize(
    "parameters",
    [
        physiology.PhysiologyParameters(absorption_rate_per_hour=0),
        physiology.PhysiologyParameters(oral_bioavailability=1.1),
        physiology.PhysiologyParameters(clearance_liters_per_hour=float("inf")),
        physiology.PhysiologyParameters(distribution_volume_liters=-1),
    ],
)
def test_invalid_parameters_fail_closed(parameters: physiology.PhysiologyParameters) -> None:
    with pytest.raises(ValueError, match=r"parameter|bioavailability"):
        physiology.concentration_nmol_per_liter(
            amount_mg=Decimal("10"), elapsed_hours=1, parameters=parameters
        )
