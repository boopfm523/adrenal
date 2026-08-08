"""Time handling for recorded events.

Plan section 6: *store UTC time for comparison plus the original local time, IANA
timezone, and UTC offset so daylight-saving changes and travel remain interpretable.*

Why all four, when three are derivable from the instant plus the zone:

* The **instant** (``occurred_at``) is the only thing safe to compare or sort across
  travel and DST.
* The **local wall time** is what the person actually experienced. "I took it at 7am"
  stays true whether they were in London or Tokyo.
* The **IANA zone** lets a later reader re-derive the local time -- but only under
  *today's* tz database. Zone rules change retroactively.
* The **stored offset** is what makes the record survive that: if a government moves a
  DST boundary next year, the recomputed offset would silently shift a historical dose
  by an hour. The stored offset pins what was actually true at capture time.

The redundancy is deliberate, and :func:`resolve_event_time` is the only supported way
to produce a consistent set. :func:`verify_consistency` checks a stored set is still
coherent, which is how a tz-database change becomes a visible data-quality finding
rather than a silent rewrite of history.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta, tzinfo
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


class AmbiguousLocalTimeError(ValueError):
    """A local wall time that occurs twice, in the hour repeated by a DST fall-back.

    Never guessed. SAFE-13 requires ambiguity to be surfaced, and picking one of the
    two instants silently would place a dose an hour from where it happened.
    """


class NonExistentLocalTimeError(ValueError):
    """A local wall time skipped by a DST spring-forward. It never happened."""


class UnknownTimezoneError(ValueError):
    """The IANA zone name is not in the tz database."""


@dataclass(frozen=True, slots=True)
class EventTime:
    """A fully resolved point in time for an event.

    ``local_time`` is naive on purpose: it is a wall-clock reading, and the zone and
    offset beside it are the authority. Attaching a tzinfo would invite code to
    re-derive the offset under a future tz database.
    """

    occurred_at: datetime  # aware, always UTC
    local_time: datetime  # naive wall time as experienced
    timezone: str  # IANA name, e.g. "Europe/London"
    utc_offset_minutes: int  # what the offset actually was, at capture

    def __post_init__(self) -> None:
        if self.occurred_at.tzinfo is not UTC:
            raise ValueError("occurred_at must be timezone-aware and in UTC")
        if self.local_time.tzinfo is not None:
            raise ValueError("local_time must be naive; the zone and offset carry the context")


def load_zone(timezone: str) -> tzinfo:
    try:
        return ZoneInfo(timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise UnknownTimezoneError(f"unknown IANA timezone: {timezone!r}") from exc


def resolve_event_time(
    local_time: datetime,
    timezone: str,
    *,
    fold: int | None = None,
) -> EventTime:
    """Resolve a local wall time in a zone into a complete :class:`EventTime`.

    ``fold`` disambiguates a repeated hour: ``0`` selects the first occurrence (before
    the clocks go back), ``1`` the second. It must be supplied explicitly for an
    ambiguous time -- omitting it raises rather than guessing, because the user is the
    only one who knows which one they meant.
    """
    if local_time.tzinfo is not None:
        raise ValueError("local_time must be naive; pass the zone separately")

    zone = load_zone(timezone)

    # Order matters: a skipped time also has two differing folds, so it would be
    # misreported as ambiguous if this check ran second.
    if is_nonexistent(local_time, timezone):
        raise NonExistentLocalTimeError(
            f"{local_time.isoformat()} does not exist in {timezone} "
            f"(skipped by a DST spring-forward)"
        )

    if fold is None:
        if is_ambiguous(local_time, timezone):
            raise AmbiguousLocalTimeError(
                f"{local_time.isoformat()} occurs twice in {timezone} (DST fall-back); "
                f"pass fold=0 for the first occurrence or fold=1 for the second"
            )
        fold = 0

    aware = local_time.replace(tzinfo=zone, fold=fold)
    offset = aware.utcoffset()
    if offset is None:  # pragma: no cover -- ZoneInfo always supplies an offset
        raise UnknownTimezoneError(f"{timezone!r} produced no UTC offset")

    return EventTime(
        occurred_at=aware.astimezone(UTC),
        local_time=local_time,
        timezone=timezone,
        utc_offset_minutes=int(offset.total_seconds() // 60),
    )


def is_ambiguous(local_time: datetime, timezone: str) -> bool:
    """True if this wall time occurs twice (the repeated hour of a DST fall-back).

    A *skipped* wall time also has two differing folds, so it is excluded here --
    otherwise an hour that never happened would be reported as one that happened twice.
    """
    zone = load_zone(timezone)
    first = local_time.replace(tzinfo=zone, fold=0)
    second = local_time.replace(tzinfo=zone, fold=1)
    if first.utcoffset() == second.utcoffset():
        return False
    return not is_nonexistent(local_time, timezone)


def is_nonexistent(local_time: datetime, timezone: str) -> bool:
    """True if this wall time is skipped by a DST spring-forward.

    Detected by round-tripping through UTC: a skipped wall time maps to an instant
    whose local representation is a *different* wall time.
    """
    zone = load_zone(timezone)
    aware = local_time.replace(tzinfo=zone, fold=0)
    return aware.astimezone(UTC).astimezone(zone).replace(tzinfo=None) != local_time


def from_instant(occurred_at: datetime, timezone: str) -> EventTime:
    """Resolve an instant (e.g. from a provider import) into a full :class:`EventTime`."""
    if occurred_at.tzinfo is None:
        raise ValueError("occurred_at must be timezone-aware")

    zone = load_zone(timezone)
    utc = occurred_at.astimezone(UTC)
    local = utc.astimezone(zone)
    offset = local.utcoffset() or timedelta(0)

    return EventTime(
        occurred_at=utc,
        local_time=local.replace(tzinfo=None),
        timezone=timezone,
        utc_offset_minutes=int(offset.total_seconds() // 60),
    )


def verify_consistency(event_time: EventTime) -> bool:
    """True if the stored fields still agree with each other.

    Recomputes the local time from the stored instant and the *stored offset* -- not
    from the tz database -- so this stays true regardless of later tz-rule changes.
    A False result means the record was written inconsistently, and is a data-quality
    finding rather than something to silently repair.
    """
    expected_local = event_time.occurred_at + timedelta(minutes=event_time.utc_offset_minutes)
    return expected_local.replace(tzinfo=None) == event_time.local_time


def offset_matches_tz_database(event_time: EventTime) -> bool:
    """True if the stored offset still matches what the tz database says today.

    A False result does not mean the record is wrong -- it means the zone's rules
    changed since capture. The stored offset remains authoritative; this surfaces the
    divergence so a report can disclose it.
    """
    zone = load_zone(event_time.timezone)
    current = event_time.occurred_at.astimezone(zone).utcoffset() or timedelta(0)
    return int(current.total_seconds() // 60) == event_time.utc_offset_minutes
