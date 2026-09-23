"""Read the timezone stay ledger, and record that the owner has moved.

The web counterpart to ``/tz`` in Telegram. Both write the same ledger through
:mod:`healthcurve.identity.timezones`, so the zone a dose is recorded in does not
depend on which surface recorded it.

Nothing here is inferred. A browser can tell the page its zone, but only a POST the
owner asked for changes the ledger -- an automatic switch would silently reinterpret
every subsequent entry on the strength of a laptop that happened to be set wrong.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.events.timekeeping import from_instant, timezone_abbreviation
from healthcurve.identity import places, timezones
from healthcurve.identity.models import Owner, TimezoneStay, TimezoneStaySource

router = APIRouter(prefix="/settings/timezone", tags=["settings"])

#: Enough to show "where have I been lately" without turning a settings panel into a
#: travel history nobody asked to read.
_HISTORY_LIMIT = 20


class TimezoneStayOut(BaseModel):
    id: uuid.UUID
    timezone: str
    #: Abbreviation in force when the stay began, e.g. ``CDT``. A zone name alone does
    #: not make a wrong guess obvious; an abbreviation usually does.
    abbreviation: str
    started_at: datetime
    #: The same instant as the wall clock read where the owner then was.
    started_at_local: datetime
    utc_offset_minutes: int
    source: TimezoneStaySource
    label: str | None


class TimezoneSettingsOut(BaseModel):
    """Where the owner is now, where home is, and how they got here."""

    current_timezone: str
    current_abbreviation: str
    current_local_time: datetime
    current_utc_offset_minutes: int
    #: ``owner.default_timezone``. Unchanged by travel: it is where the owner lives,
    #: not where they are.
    home_timezone: str
    #: True when the two agree, which is the ordinary case and the one the UI should
    #: say nothing about.
    is_home: bool
    stays: list[TimezoneStayOut]


class TimezoneChangeOut(TimezoneSettingsOut):
    #: False when the zone in force was already the requested one. Restating where you
    #: are is not travel, so no row is written and the ledger stays readable.
    recorded: bool


class TimezoneChangeIn(BaseModel):
    #: An IANA name (``America/Denver``) or a place the alias table knows ("Denver").
    timezone: str = Field(min_length=1, max_length=120)
    #: When the stay began. Defaults to now; may not be in the future, because a stay
    #: that has not started yet would claim entries made before the owner arrives.
    started_at: datetime | None = None
    label: str | None = Field(default=None, max_length=120)


def _invalid(code: str, message: str, **extra: object) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "message": message, **extra},
    )


def _stay_out(stay: TimezoneStay) -> TimezoneStayOut:
    event_time = from_instant(stay.started_at, stay.timezone)
    return TimezoneStayOut(
        id=stay.id,
        timezone=stay.timezone,
        abbreviation=timezone_abbreviation(stay.timezone, stay.started_at),
        started_at=event_time.occurred_at,
        started_at_local=event_time.local_time,
        utc_offset_minutes=event_time.utc_offset_minutes,
        source=stay.source,
        label=stay.label,
    )


def _settings(session: Session, owner: Owner, *, now: datetime) -> TimezoneSettingsOut:
    zone = timezones.current_zone(session, owner, now=now)
    here = from_instant(now, zone)
    return TimezoneSettingsOut(
        current_timezone=zone,
        current_abbreviation=timezone_abbreviation(zone, now),
        current_local_time=here.local_time,
        current_utc_offset_minutes=here.utc_offset_minutes,
        home_timezone=owner.default_timezone,
        is_home=zone == owner.default_timezone,
        stays=[_stay_out(stay) for stay in timezones.history(session, owner, limit=_HISTORY_LIMIT)],
    )


@router.get("", response_model=TimezoneSettingsOut)
def read_timezone(session: DbSession, owner: CurrentOwner) -> TimezoneSettingsOut:
    return _settings(session, owner, now=datetime.now(UTC))


@router.post("", response_model=TimezoneChangeOut, dependencies=[Depends(require_csrf)])
def record_timezone(
    payload: TimezoneChangeIn, session: DbSession, owner: CurrentOwner
) -> TimezoneChangeOut:
    """Record a stay from ``started_at`` onwards.

    Rejects before writing anything: an unknown place or a start in the future leaves
    the ledger exactly as it was.
    """
    now = datetime.now(UTC)
    started_at = payload.started_at or now
    if started_at.tzinfo is None:
        raise _invalid(
            "naive_started_at", "started_at must carry a UTC offset; the zone is what this sets"
        )
    started_at = started_at.astimezone(UTC)
    if started_at > now:
        raise _invalid("future_started_at", "a stay cannot begin in the future")

    try:
        zone = places.resolve(payload.timezone, at=started_at)
    except places.AmbiguousPlaceError as exc:
        raise _invalid(
            "ambiguous_place",
            f"{exc.place!r} matches more than one timezone",
            candidates=list(exc.candidates),
        ) from exc
    if zone is None:
        raise _invalid("invalid_timezone", f"unknown timezone or place: {payload.timezone!r}")

    stay = timezones.record_stay(
        session,
        owner,
        zone,
        source=TimezoneStaySource.WEB,
        started_at=started_at,
        label=payload.label,
    )
    session.flush()
    return TimezoneChangeOut(
        recorded=stay is not None, **_settings(session, owner, now=now).model_dump()
    )
