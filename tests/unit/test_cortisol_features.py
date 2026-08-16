from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any

import pytest

from healthcurve.analytics.cortisol_features import (
    FEATURE_ID,
    FEATURE_REVISION,
    FeatureDose,
    FeatureSample,
    FeatureSymptom,
    derive_daily_features,
)


def sample(
    hour: float,
    *,
    modeled: str,
    regular: str | None = None,
    stress: str = "0",
    p5: str = "4",
    p25: str = "6",
    p50: str = "8",
    p95: str = "9",
    start: datetime = datetime(2026, 8, 11, tzinfo=UTC),
) -> FeatureSample:
    return FeatureSample(
        occurred_at=start + timedelta(hours=hour),
        modeled=Decimal(modeled),
        regular=Decimal(modeled if regular is None else regular),
        stress=Decimal(stress),
        p5=Decimal(p5),
        p25=Decimal(p25),
        p50=Decimal(p50),
        p95=Decimal(p95),
    )


def dose(identity: int, hour: float, category: str = "scheduled") -> FeatureDose:
    return FeatureDose(
        dose_event_id=uuid.UUID(int=identity),
        occurred_at=datetime(2026, 8, 11, tzinfo=UTC) + timedelta(hours=hour),
        category=category,
    )


def derive(
    *,
    samples: list[FeatureSample],
    doses: list[FeatureDose] | None = None,
    symptoms: list[FeatureSymptom] | None = None,
    day_start: datetime = datetime(2026, 8, 11, tzinfo=UTC),
    day_end: datetime = datetime(2026, 8, 11, 3, tzinfo=UTC),
    analyzed_through: datetime | None = None,
    wake_at: datetime | None = None,
    sleep_onset_at: datetime | None = None,
) -> dict[str, Any]:
    return derive_daily_features(
        day=date(2026, 8, 11),
        timezone="UTC",
        day_start=day_start,
        day_end=day_end,
        analyzed_through=analyzed_through or day_end,
        samples=samples,
        doses=doses or [],
        symptoms=symptoms or [],
        wake_at=wake_at,
        sleep_onset_at=sleep_onset_at,
        source_model_sha256="a" * 64,
        reference_revision="hc-wake-reference-v1.0.0",
    )


def test_derives_piecewise_duration_auc_fall_and_overshoot() -> None:
    samples = [
        sample(0, modeled="0"),
        sample(1, modeled="5"),
        sample(2, modeled="10", regular="5", stress="5"),
        sample(3, modeled="0"),
    ]

    result = derive(samples=samples)

    assert result["available"] is True
    assert result["feature_id"] == FEATURE_ID
    assert result["feature_revision"] == FEATURE_REVISION
    assert result["day_state"] == "complete"
    assert result["time_below_p5_minutes"] == Decimal("72.0000")
    assert result["time_below_p25_minutes"] == Decimal("108.0000")
    assert result["expected_pre_wake_excluded_minutes"] == Decimal("0.0000")
    assert result["auc"] == {
        "modeled_free_nmol_l_hours": Decimal("15.0000"),
        "regular_modeled_free_nmol_l_hours": Decimal("10.0000"),
        "stress_modeled_free_nmol_l_hours": Decimal("5.0000"),
        "reference_p50_nmol_l_hours": Decimal("24.0000"),
        "modeled_minus_reference_p50_nmol_l_hours": Decimal("-9.0000"),
        "modeled_to_reference_p50_ratio": Decimal("0.6250"),
    }
    assert result["maximum_fall"] == {
        "magnitude_nmol_l_per_hour": Decimal("10.0000"),
        "interval_started_at": datetime(2026, 8, 11, 2, tzinfo=UTC),
        "interval_ended_at": datetime(2026, 8, 11, 3, tzinfo=UTC),
        "from_modeled_free_cortisol_nmol_l": Decimal("10.0000"),
        "to_modeled_free_cortisol_nmol_l": Decimal("0.0000"),
    }
    assert result["p95_overshoot"] == {
        "duration_minutes": Decimal("18.0000"),
        "maximum_nmol_l": Decimal("1.0000"),
        "maximum_at": datetime(2026, 8, 11, 2, tzinfo=UTC),
    }


def test_expected_pre_wake_interval_is_reported_and_excluded_from_below_time() -> None:
    start = datetime(2026, 8, 11, tzinfo=UTC)
    result = derive(
        samples=[
            sample(0, modeled="0"),
            sample(1, modeled="5"),
            sample(2, modeled="10"),
            sample(3, modeled="0"),
        ],
        doses=[dose(1, 0.5), dose(2, 2.5)],
        wake_at=start + timedelta(minutes=15),
    )

    assert result["expected_pre_wake_excluded_minutes"] == Decimal("30.0000")
    assert result["comparison_minutes"] == Decimal("150.0000")
    assert result["time_below_p5_minutes"] == Decimal("42.0000")
    assert result["time_below_p25_minutes"] == Decimal("78.0000")


def test_inter_dose_troughs_and_symptom_context_keep_regular_stress_attribution() -> None:
    start = datetime(2026, 8, 11, tzinfo=UTC)
    symptom_id = uuid.UUID(int=90)
    result = derive(
        samples=[
            sample(0, modeled="2"),
            sample(1, modeled="8"),
            sample(2, modeled="4", regular="2", stress="2"),
            sample(3, modeled="9", regular="4", stress="5"),
        ],
        doses=[dose(1, 0), dose(2, 2, "stress")],
        symptoms=[
            FeatureSymptom(
                symptom_event_id=symptom_id,
                occurred_at=start + timedelta(hours=2, minutes=30),
                name="synthetic symptom",
                severity=4,
            )
        ],
    )

    assert result["inter_dose_troughs"] == [
        {
            "previous_dose_event_id": uuid.UUID(int=1),
            "next_dose_event_id": uuid.UUID(int=2),
            "occurred_at": start,
            "modeled_free_cortisol_nmol_l": Decimal("2.0000"),
            "regular_modeled_free_cortisol_nmol_l": Decimal("2.0000"),
            "stress_modeled_free_cortisol_nmol_l": Decimal("0.0000"),
            "reference_p5_nmol_l": Decimal("4.0000"),
            "reference_p25_nmol_l": Decimal("6.0000"),
            "reference_p50_nmol_l": Decimal("8.0000"),
            "depth_below_p50_nmol_l": Decimal("6.0000"),
        }
    ]
    assert result["symptom_contexts"] == [
        {
            "symptom_event_id": symptom_id,
            "occurred_at": start + timedelta(hours=2, minutes=30),
            "name": "synthetic symptom",
            "severity": 4,
            "tracking_category": None,
            "tracking_category_revision": None,
            "previous_supported_dose_event_ids": [uuid.UUID(int=2)],
            "previous_dose_categories": ["stress"],
            "minutes_since_previous_supported_dose": Decimal("30.0000"),
            "modeled_free_cortisol_nmol_l": Decimal("6.5000"),
            "regular_modeled_free_cortisol_nmol_l": Decimal("3.0000"),
            "stress_modeled_free_cortisol_nmol_l": Decimal("3.5000"),
            "reference_p5_nmol_l": Decimal("4.0000"),
            "reference_p50_nmol_l": Decimal("8.0000"),
            "reference_p95_nmol_l": Decimal("9.0000"),
        }
    ]


def test_partial_day_clips_interpolated_end_and_changes_fingerprint() -> None:
    start = datetime(2026, 8, 11, tzinfo=UTC)
    samples = [sample(0, modeled="0"), sample(1, modeled="10"), sample(2, modeled="0")]
    partial = derive(
        samples=samples,
        day_end=start + timedelta(hours=2),
        analyzed_through=start + timedelta(minutes=90),
    )
    complete = derive(samples=samples, day_end=start + timedelta(hours=2))

    assert partial["day_state"] == "partial"
    assert partial["elapsed_hours"] == Decimal("1.5000")
    assert partial["auc"]["modeled_free_nmol_l_hours"] == Decimal("8.7500")
    assert partial["source_revision_sha256"] != complete["source_revision_sha256"]


@pytest.mark.parametrize(
    ("day_start", "day_end", "expected_hours"),
    [
        (
            datetime(2026, 3, 8, 5, tzinfo=UTC),
            datetime(2026, 3, 9, 4, tzinfo=UTC),
            Decimal("23.0000"),
        ),
        (
            datetime(2026, 11, 1, 4, tzinfo=UTC),
            datetime(2026, 11, 2, 5, tzinfo=UTC),
            Decimal("25.0000"),
        ),
    ],
)
def test_uses_real_elapsed_dst_day(
    day_start: datetime, day_end: datetime, expected_hours: Decimal
) -> None:
    result = derive(
        samples=[
            sample(0, modeled="5", start=day_start),
            sample(float(expected_hours), modeled="5", start=day_start),
        ],
        day_start=day_start,
        day_end=day_end,
        analyzed_through=day_end,
    )

    assert result["elapsed_hours"] == expected_hours
    assert result["auc"]["modeled_free_nmol_l_hours"] == expected_hours * Decimal(5)


def test_unavailable_elapsed_window_preserves_missingness() -> None:
    start = datetime(2026, 8, 11, tzinfo=UTC)
    result = derive(
        samples=[],
        day_end=start + timedelta(days=1),
        analyzed_through=start,
    )

    assert result["available"] is False
    assert result["day_state"] == "partial"
    assert result["missing_inputs"] == ["elapsed_comparison_window"]
    assert result["time_below_p5_minutes"] is None
    assert result["auc"] is None
