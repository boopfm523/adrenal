import uuid
from datetime import date
from decimal import Decimal
from typing import Any, cast

from healthcurve.analytics.patterns import build_response


def test_pattern_range_preserves_per_day_plan_and_model_versions() -> None:
    first_plan = uuid.UUID("11111111-1111-4111-8111-111111111111")
    second_plan = uuid.UUID("22222222-2222-4222-8222-222222222222")
    days: list[dict[str, object]] = [
        {
            "date": date(2026, 8, 1),
            "feature_version": "hc-daily-pattern-v1",
            "exposure_model_version": "hc-exposure-v1",
            "dose_plan_version_ids": [first_plan],
            "exposure_auc_reu_hours": Decimal("10"),
            "average_symptom_severity": Decimal("2"),
            "wearables": [],
        },
        {
            "date": date(2026, 8, 2),
            "feature_version": "hc-daily-pattern-v1",
            "exposure_model_version": "hc-exposure-v2-synthetic",
            "dose_plan_version_ids": [first_plan, second_plan],
            "exposure_auc_reu_hours": Decimal("12"),
            "average_symptom_severity": None,
            "wearables": [],
        },
    ]

    result = build_response(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 2),
        timezone="UTC",
        days=days,
    )

    assert result["exposure_model_versions"] == [
        "hc-exposure-v1",
        "hc-exposure-v2-synthetic",
    ]
    assert result["days"] == days
    summary = cast(dict[str, Any], result["longitudinal_summary"])
    assert summary["minimum_observed_days_for_trend"] == 7
    assert summary["model_version_periods"] == [
        {
            "date_from": date(2026, 8, 1),
            "date_to": date(2026, 8, 1),
            "feature_version": "hc-daily-pattern-v1",
            "exposure_model_version": "hc-exposure-v1",
        },
        {
            "date_from": date(2026, 8, 2),
            "date_to": date(2026, 8, 2),
            "feature_version": "hc-daily-pattern-v1",
            "exposure_model_version": "hc-exposure-v2-synthetic",
        },
    ]
    exposure = cast(list[dict[str, Any]], summary["metrics"])[0]
    assert exposure["median"] == Decimal("11.0000")
    assert exposure["first_to_last_change"] is None
    assert exposure["trend_eligible"] is False
