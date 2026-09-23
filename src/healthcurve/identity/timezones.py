"""Which zone the owner was living in, and when.

``owner.default_timezone`` was the only zone the application had. Every capture path
resolved against it, so an entry made in Chicago by someone whose home zone is
``America/New_York`` was recorded and displayed an hour from where it happened -- and a
stated wall time ("took it at 8am") was converted using the *home* offset, putting the
instant an hour from the dose. On a cortisol curve that is not a cosmetic error.

:class:`~healthcurve.identity.models.TimezoneStay` replaces the single value with a
ledger. A stay is open-ended and ends when the next one begins, so the zone is a step
function over time with no representable gap or overlap. There is no backfill: an
instant before the first stay resolves to ``owner.default_timezone``, which is exactly
today's behaviour, so introducing the ledger reinterprets no existing event.

See ADR-0038 for why this is a ledger rather than an editable field, why there is no
backfill, and what a local day means across a trip.

This module sits in ``identity``, which is below ``events`` in the ADR-0002 layering,
so it cannot reach :mod:`healthcurve.events.timekeeping` and works with
:class:`zoneinfo.ZoneInfo` directly. The duplication is one validating call, and the
alternative -- an upward import -- would invert the dependency stack.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.identity.models import Owner, TimezoneStay, TimezoneStaySource

#: A stated wall time is converted with a guessed zone, and the zone that guess lands
#: in is then used to convert again. Two passes settle every case the ledger can
#: produce: the first pass can only be wrong if the instant crossed a stay boundary,
#: and the second pass converts inside the zone that boundary leads to. A third pass
#: would only differ for stays shorter than the offset between them, which
#: :func:`record_stay` cannot produce from a monotonic clock.
_CONVERGENCE_PASSES: Final = 2


class UnknownTimezoneError(ValueError):
    """The IANA zone name is not in the tz database.

    Distinct from :class:`healthcurve.events.timekeeping.UnknownTimezoneError` only
    because ``identity`` may not import ``events``. Callers above both layers that
    validate a zone *and* resolve an event time should expect either.
    """


def load_zone(timezone: str) -> ZoneInfo:
    """Return the tz-database zone, raising rather than falling back to UTC.

    A silent UTC fallback is the failure mode worth designing out: it produces a
    plausible-looking record that is wrong by the whole offset.
    """
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnknownTimezoneError(f"unknown IANA timezone: {timezone!r}") from exc


def zone_at(session: Session, owner: Owner, instant: datetime) -> str:
    """The IANA zone the owner was in at an instant.

    Falls back to the owner's home zone for any instant before the first recorded
    stay, which is what keeps this non-destructive for everything already captured.
    """
    if instant.tzinfo is None:
        raise ValueError("instant must be timezone-aware")

    stay = session.scalar(
        select(TimezoneStay)
        .where(TimezoneStay.owner_id == owner.id, TimezoneStay.started_at <= instant)
        .order_by(TimezoneStay.started_at.desc())
        .limit(1)
    )
    return stay.timezone if stay is not None else owner.default_timezone


def current_zone(session: Session, owner: Owner, *, now: datetime | None = None) -> str:
    """The zone the owner is in right now."""
    return zone_at(session, owner, now or datetime.now(UTC))


def zone_for_local_time(
    session: Session,
    owner: Owner,
    local_time: datetime,
    *,
    now: datetime | None = None,
) -> str:
    """The zone a stated wall time should be interpreted in.

    Backfilling is the hard case. "I took it at 8am" sent from Chicago at 6pm means
    8am *Chicago* time; the same message sent an hour after landing may mean 8am in the
    zone left behind. The wall time alone cannot say which, because choosing the zone
    is what fixes the instant, and the instant is what selects the zone.

    Resolved by converging: convert under the current zone, look up the zone in force
    at the resulting instant, and convert again under that. See
    :data:`_CONVERGENCE_PASSES` for why two passes suffice.

    ``fold=0`` is used deliberately. During a repeated DST hour the two candidate
    instants are an hour apart and can only select different stays if a stay began
    inside that hour; the caller still resolves the authoritative
    :class:`~healthcurve.events.timekeeping.EventTime` itself, and that path surfaces
    the ambiguity rather than guessing. What is returned here is only *which zone to
    ask*.
    """
    if local_time.tzinfo is not None:
        raise ValueError("local_time must be naive; the zone is what this resolves")

    candidate = current_zone(session, owner, now=now)
    for _ in range(_CONVERGENCE_PASSES):
        instant = local_time.replace(tzinfo=load_zone(candidate), fold=0).astimezone(UTC)
        resolved = zone_at(session, owner, instant)
        if resolved == candidate:
            break
        candidate = resolved
    return candidate


def record_stay(
    session: Session,
    owner: Owner,
    timezone: str,
    *,
    source: TimezoneStaySource,
    started_at: datetime | None = None,
    label: str | None = None,
) -> TimezoneStay | None:
    """Record that the owner is in ``timezone`` from ``started_at`` onwards.

    Returns ``None`` when the zone in force at that instant is already this one.
    Saying "I'm in Chicago" twice is a restatement, not travel, and a ledger that
    accumulated a row per restatement would make its own history unreadable.

    Raises :class:`UnknownTimezoneError` before touching the session, so a typo leaves
    no trace.
    """
    load_zone(timezone)
    started_at = (started_at or datetime.now(UTC)).astimezone(UTC)

    if zone_at(session, owner, started_at) == timezone:
        return None

    existing = session.scalar(
        select(TimezoneStay).where(
            TimezoneStay.owner_id == owner.id, TimezoneStay.started_at == started_at
        )
    )
    if existing is not None:
        # The unique constraint forbids two stays at one instant, and a correction
        # arriving in the same second is a correction, not a second journey.
        existing.timezone = timezone
        existing.source = source
        existing.label = label
        return existing

    stay = TimezoneStay(
        id=uuid.uuid4(),
        owner_id=owner.id,
        timezone=timezone,
        started_at=started_at,
        source=source,
        label=label,
    )
    session.add(stay)
    return stay


def history(session: Session, owner: Owner, *, limit: int = 10) -> list[TimezoneStay]:
    """Recent stays, most recent first."""
    return list(
        session.scalars(
            select(TimezoneStay)
            .where(TimezoneStay.owner_id == owner.id)
            .order_by(TimezoneStay.started_at.desc())
            .limit(limit)
        )
    )
