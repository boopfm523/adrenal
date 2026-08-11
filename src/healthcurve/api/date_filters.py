"""Shared local-calendar date filtering for experienced event times."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException, status


@dataclass(frozen=True, slots=True)
class LocalDateWindow:
    timezone: str
    start: datetime | None
    end_exclusive: datetime | None


def local_date_window(
    *,
    profile_timezone: str,
    timezone: str | None,
    date_from: date | None,
    date_to: date | None,
) -> LocalDateWindow:
    """Convert inclusive local dates into DST-aware instant boundaries."""
    zone_name = timezone or profile_timezone
    try:
        zone = ZoneInfo(zone_name)
    except ZoneInfoNotFoundError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_timezone"},
        ) from exc
    if date_from is not None and date_to is not None and date_from > date_to:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail={"code": "invalid_local_date_range"},
        )
    return LocalDateWindow(
        timezone=zone_name,
        start=None if date_from is None else datetime.combine(date_from, time.min, tzinfo=zone),
        end_exclusive=(
            None
            if date_to is None
            else datetime.combine(date_to + timedelta(days=1), time.min, tzinfo=zone)
        ),
    )
