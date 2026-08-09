"""Authenticated deterministic analytics endpoints."""

from __future__ import annotations

from datetime import date
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import APIRouter, HTTPException

from healthcurve.analytics import service
from healthcurve.api.deps import CurrentOwner, DbSession
from healthcurve.api.schemas import AnalyticsSummaryOut

router = APIRouter(tags=["analytics"])
MAX_RANGE_DAYS = 366


@router.get("/analytics/summary", response_model=AnalyticsSummaryOut)
def analytics_summary(
    session: DbSession,
    owner: CurrentOwner,
    date_from: date,
    date_to: date,
    timezone: str | None = None,
):
    zone_name = timezone or owner.default_timezone
    try:
        ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail="invalid timezone") from exc
    if date_to < date_from:
        raise HTTPException(status_code=422, detail="date_to must be on or after date_from")
    if (date_to - date_from).days + 1 > MAX_RANGE_DAYS:
        raise HTTPException(status_code=422, detail=f"range cannot exceed {MAX_RANGE_DAYS} days")
    return service.summary_for_owner(
        session,
        owner_id=owner.id,
        date_from=date_from,
        date_to=date_to,
        timezone=zone_name,
    )
