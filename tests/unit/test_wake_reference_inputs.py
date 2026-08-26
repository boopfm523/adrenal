from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

from healthcurve.analytics.wake_reference_inputs import signed_minutes_from_wake


def test_signed_minutes_accepts_aware_wake_and_naive_local_dose() -> None:
    zone = ZoneInfo("America/New_York")

    assert (
        signed_minutes_from_wake(
            datetime(2026, 8, 15, 6, 30, tzinfo=zone),
            datetime(2026, 8, 15, 6, 45),  # noqa: DTZ001 - medication wall time
            timezone="America/New_York",
        )
        == 15
    )


def test_signed_minutes_uses_elapsed_time_across_dst_fall_back() -> None:
    zone = ZoneInfo("America/New_York")

    assert (
        signed_minutes_from_wake(
            datetime(2026, 11, 1, 1, 30, tzinfo=zone, fold=0),
            datetime(2026, 11, 1, 1, 45, fold=1),  # noqa: DTZ001 - medication wall time
            timezone="America/New_York",
        )
        == 75
    )


def test_signed_minutes_preserves_missing_observations() -> None:
    assert (
        signed_minutes_from_wake(
            None,
            datetime(2026, 8, 15, 6, 45),  # noqa: DTZ001 - medication wall time
            timezone="America/New_York",
        )
        is None
    )
