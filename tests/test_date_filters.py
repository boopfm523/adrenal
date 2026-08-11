from datetime import date, timedelta

import pytest
from fastapi import HTTPException

from healthcurve.api.date_filters import local_date_window


def test_local_date_window_is_inclusive_and_dst_aware() -> None:
    spring = local_date_window(
        profile_timezone="UTC",
        timezone="America/New_York",
        date_from=date(2026, 3, 8),
        date_to=date(2026, 3, 8),
    )
    fall = local_date_window(
        profile_timezone="UTC",
        timezone="America/New_York",
        date_from=date(2026, 11, 1),
        date_to=date(2026, 11, 1),
    )

    assert spring.start is not None and spring.end_exclusive is not None
    assert fall.start is not None and fall.end_exclusive is not None
    assert (
        spring.end_exclusive.timestamp() - spring.start.timestamp()
        == timedelta(hours=23).total_seconds()
    )
    assert (
        fall.end_exclusive.timestamp() - fall.start.timestamp()
        == timedelta(hours=25).total_seconds()
    )


@pytest.mark.parametrize(
    ("timezone", "date_from", "date_to", "code"),
    [
        ("Not/AZone", None, None, "invalid_timezone"),
        ("UTC", date(2026, 8, 11), date(2026, 8, 10), "invalid_local_date_range"),
    ],
)
def test_local_date_window_rejects_invalid_inputs(
    timezone: str, date_from: date | None, date_to: date | None, code: str
) -> None:
    with pytest.raises(HTTPException) as raised:
        local_date_window(
            profile_timezone="UTC",
            timezone=timezone,
            date_from=date_from,
            date_to=date_to,
        )
    assert raised.value.status_code == 422
    assert raised.value.detail == {"code": code}
