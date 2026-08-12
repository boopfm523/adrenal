import math
import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

import pytest
from hypothesis import given
from hypothesis import strategies as st

from healthcurve.analytics.exposure import (
    PEAK_TIME_HOURS,
    ExposureDose,
    build_curve,
    contribution_reu,
    exclusion_reason,
    normalized_shape,
)
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
        supersedes_id=None,
    )


@pytest.mark.parametrize(
    ("elapsed", "expected"),
    [
        (-1 / 60, 0.0),
        (0.0, 0.0),
        (0.25, 0.559734294),
        (0.5, 0.844986280),
        (1.0, 0.999999371),
        (2.0, 0.800490809),
        (4.0, 0.368824685),
        (8.0, 0.072319888),
        (12.0, 0.014156411),
    ],
)
def test_v1_shape_matches_adr_gold_values(elapsed: float, expected: float) -> None:
    assert normalized_shape(elapsed) == pytest.approx(expected, abs=1e-9)
    assert normalized_shape(elapsed) >= 0


def test_v1_peak_is_normalized_to_one() -> None:
    assert PEAK_TIME_HOURS == pytest.approx(0.998757738, abs=1e-9)
    assert normalized_shape(PEAK_TIME_HOURS) == pytest.approx(1.0, abs=1e-12)


@given(st.floats(min_value=0, max_value=24, allow_nan=False, allow_infinity=False))
def test_v1_shape_is_nonnegative_and_never_exceeds_its_normalized_peak(
    elapsed: float,
) -> None:
    assert 0.0 <= normalized_shape(elapsed) <= 1.0 + 1e-12


@given(
    st.integers(min_value=1, max_value=100),
    st.integers(min_value=1, max_value=100),
    st.floats(min_value=0, max_value=24, allow_nan=False, allow_infinity=False),
)
def test_same_instant_contributions_are_additive(
    first_amount: int, second_amount: int, elapsed: float
) -> None:
    administered = datetime(2026, 8, 11, tzinfo=UTC)
    instant = administered + timedelta(hours=elapsed)
    first = dose(identity=91, occurred_at=administered, amount=str(first_amount))
    second = dose(identity=92, occurred_at=administered, amount=str(second_amount))
    combined = dose(identity=93, occurred_at=administered, amount=str(first_amount + second_amount))

    assert math.fsum([contribution_reu(first, instant), contribution_reu(second, instant)]) == (
        pytest.approx(contribution_reu(combined, instant), abs=1e-10)
    )


def test_close_and_equal_timestamp_doses_sum_without_deduplication() -> None:
    first_at = datetime(2026, 8, 11, 7, tzinfo=UTC)
    first = dose(identity=1, occurred_at=first_at, amount="10")
    close = dose(identity=2, occurred_at=first_at + timedelta(minutes=1), amount="5")
    same = dose(identity=3, occurred_at=first_at, amount="5")
    instant = first_at + timedelta(hours=PEAK_TIME_HOURS)

    close_curve = cast(
        dict[str, Any],
        build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[close, first]),
    )
    same_curve = cast(
        dict[str, Any],
        build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[same, first]),
    )
    close_sample = next(row for row in close_curve["samples"] if row["occurred_at"] == instant)
    same_sample = next(row for row in same_curve["samples"] if row["occurred_at"] == instant)

    assert close_curve["supported_dose_count"] == 2
    assert close_sample["theoretical_exposure_reu"] == Decimal(
        str(math.fsum([contribution_reu(first, instant), contribution_reu(close, instant)]))
    ).quantize(Decimal("0.000000001"))
    assert same_sample["theoretical_exposure_reu"] == Decimal("15.000000000")
    assert [row["dose_event_id"] for row in same_curve["dose_markers"]] == [first.id, same.id]


def test_each_sample_splits_stress_from_every_other_supported_category_once() -> None:
    administered = datetime(2026, 8, 10, 23, 30, tzinfo=UTC)
    regular = dose(identity=31, occurred_at=administered, amount="10")
    stress = dose(
        identity=32,
        occurred_at=administered + timedelta(minutes=1),
        amount="5",
        category=DoseCategory.STRESS,
    )
    late = dose(
        identity=33,
        occurred_at=administered,
        amount="2.5",
        category=DoseCategory.LATE,
    )
    curve = cast(
        dict[str, Any],
        build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[stress, late, regular]),
    )

    assert [marker["category"] for marker in curve["dose_markers"]] == [
        DoseCategory.SCHEDULED,
        DoseCategory.LATE,
        DoseCategory.STRESS,
    ]
    for sample in curve["samples"]:
        assert sample["theoretical_exposure_reu"] == (
            sample["regular_exposure_reu"] + sample["stress_exposure_reu"]
        )
    assert curve["samples"][0]["regular_exposure_reu"] > 0
    assert curve["samples"][0]["stress_exposure_reu"] > 0


def test_curve_is_order_independent_and_includes_prior_day_carryover() -> None:
    prior = dose(identity=4, occurred_at=datetime(2026, 8, 10, 23, 30, tzinfo=UTC))
    morning = dose(identity=5, occurred_at=datetime(2026, 8, 11, 7, tzinfo=UTC))

    forward = cast(
        dict[str, Any],
        build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[prior, morning]),
    )
    reverse = cast(
        dict[str, Any],
        build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[morning, prior]),
    )

    assert forward["samples"] == reverse["samples"]
    assert forward["dose_markers"] == reverse["dose_markers"]
    assert forward["dose_markers"][0]["carryover"] is True
    assert forward["samples"][0]["theoretical_exposure_reu"] > 0


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
        build_curve(day=date(2026, 8, 11), timezone="UTC", doses=[item]),
    )

    assert exclusion_reason(item) == reason
    assert curve["supported_dose_count"] == 0
    assert curve["excluded_dose_count"] == 1
    assert curve["dose_markers"][0]["exclusion_reason"] == reason
    assert all(row["theoretical_exposure_reu"] == 0 for row in curve["samples"])


@pytest.mark.parametrize(
    ("day", "elapsed_hours"),
    [(date(2026, 3, 8), Decimal("23.0")), (date(2026, 11, 1), Decimal("25.0"))],
)
def test_local_day_uses_elapsed_dst_boundaries(day: date, elapsed_hours: Decimal) -> None:
    curve = cast(dict[str, Any], build_curve(day=day, timezone="America/New_York", doses=[]))
    instants = [row["occurred_at"] for row in curve["samples"]]

    assert curve["elapsed_hours"] == elapsed_hours
    assert instants == sorted(set(instants))
    assert curve["safety_label"].startswith("Theoretical hydrocortisone exposure")


def test_fall_back_repeated_hour_has_distinct_offsets() -> None:
    curve = cast(
        dict[str, Any],
        build_curve(day=date(2026, 11, 1), timezone="America/New_York", doses=[]),
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
