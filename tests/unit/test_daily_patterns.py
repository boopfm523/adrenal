import uuid
from datetime import date

from healthcurve.analytics.patterns import build_response


def test_pattern_range_preserves_per_day_plan_and_model_versions() -> None:
    first_plan = uuid.UUID("11111111-1111-4111-8111-111111111111")
    second_plan = uuid.UUID("22222222-2222-4222-8222-222222222222")
    days: list[dict[str, object]] = [
        {
            "date": date(2026, 8, 1),
            "exposure_model_version": "hc-exposure-v1",
            "dose_plan_version_ids": [first_plan],
        },
        {
            "date": date(2026, 8, 2),
            "exposure_model_version": "hc-exposure-v2-synthetic",
            "dose_plan_version_ids": [first_plan, second_plan],
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
