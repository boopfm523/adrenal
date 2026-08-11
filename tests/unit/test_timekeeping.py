"""SAFE-09: temporal provenance survives DST and travel.

The cases here are the ones that actually corrupt a medication record: the repeated
hour when clocks go back, the hour that never happened when they go forward, and a
record read back after the person has flown somewhere else.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from healthcurve.events.timekeeping import (
    AmbiguousLocalTimeError,
    EventTime,
    NonExistentLocalTimeError,
    UnknownTimezoneError,
    from_instant,
    is_ambiguous,
    is_nonexistent,
    offset_matches_tz_database,
    resolve_event_time,
    timezone_abbreviation,
    timezone_abbreviation_for_local_date,
    verify_consistency,
)

LONDON = "Europe/London"
NEW_YORK = "America/New_York"
TOKYO = "Asia/Tokyo"  # no DST


# ---------------------------------------------------------------------------
# Ordinary resolution
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-09")
def test_resolves_all_four_fields() -> None:
    resolved = resolve_event_time(datetime(2026, 1, 15, 7, 8), LONDON)  # noqa: DTZ001
    assert resolved.occurred_at == datetime(2026, 1, 15, 7, 8, tzinfo=UTC)
    assert resolved.local_time == datetime(2026, 1, 15, 7, 8)  # noqa: DTZ001
    assert resolved.timezone == LONDON
    assert resolved.utc_offset_minutes == 0  # GMT in January


@pytest.mark.safety("SAFE-09")
def test_summer_offset_is_recorded() -> None:
    resolved = resolve_event_time(datetime(2026, 7, 15, 7, 8), LONDON)  # noqa: DTZ001
    assert resolved.utc_offset_minutes == 60  # BST
    assert resolved.occurred_at == datetime(2026, 7, 15, 6, 8, tzinfo=UTC)


def test_timezone_abbreviation_tracks_iana_rules_at_the_referenced_instant() -> None:
    assert timezone_abbreviation(NEW_YORK, datetime(2026, 1, 15, 12, tzinfo=UTC)) == "EST"
    assert timezone_abbreviation(NEW_YORK, datetime(2026, 8, 15, 12, tzinfo=UTC)) == "EDT"
    assert timezone_abbreviation_for_local_date(NEW_YORK, date(2026, 1, 15)) == "EST"


def test_naive_input_is_required() -> None:
    with pytest.raises(ValueError, match="must be naive"):
        resolve_event_time(datetime(2026, 1, 1, 9, 0, tzinfo=UTC), LONDON)


def test_unknown_timezone_is_rejected() -> None:
    with pytest.raises(UnknownTimezoneError):
        resolve_event_time(datetime(2026, 1, 1, 9, 0), "Mars/Olympus_Mons")  # noqa: DTZ001


# ---------------------------------------------------------------------------
# DST fall-back: the repeated hour
# ---------------------------------------------------------------------------


def test_detects_the_repeated_hour() -> None:
    # 2026-10-25 01:30 happens twice in London.
    assert is_ambiguous(datetime(2026, 10, 25, 1, 30), LONDON)  # noqa: DTZ001
    assert not is_ambiguous(datetime(2026, 10, 25, 3, 30), LONDON)  # noqa: DTZ001


@pytest.mark.safety("SAFE-13")
def test_ambiguous_time_is_refused_rather_than_guessed() -> None:
    """SAFE-13: ambiguity is surfaced. Guessing would move a dose by an hour."""
    with pytest.raises(AmbiguousLocalTimeError, match="occurs twice"):
        resolve_event_time(datetime(2026, 10, 25, 1, 30), LONDON)  # noqa: DTZ001


def test_fold_selects_between_the_two_occurrences() -> None:
    local = datetime(2026, 10, 25, 1, 30)  # noqa: DTZ001
    first = resolve_event_time(local, LONDON, fold=0)
    second = resolve_event_time(local, LONDON, fold=1)

    assert first.utc_offset_minutes == 60  # still BST
    assert second.utc_offset_minutes == 0  # now GMT
    assert second.occurred_at - first.occurred_at == timedelta(hours=1)
    # Both report the same wall time, which is the point of storing it separately.
    assert first.local_time == second.local_time == local


# ---------------------------------------------------------------------------
# DST spring-forward: the hour that never happened
# ---------------------------------------------------------------------------


def test_detects_the_skipped_hour() -> None:
    # 2026-03-29 01:30 does not exist in London; clocks jump 01:00 -> 02:00.
    assert is_nonexistent(datetime(2026, 3, 29, 1, 30), LONDON)  # noqa: DTZ001
    assert not is_nonexistent(datetime(2026, 3, 29, 3, 30), LONDON)  # noqa: DTZ001


@pytest.mark.safety("SAFE-13")
def test_nonexistent_time_is_refused() -> None:
    with pytest.raises(NonExistentLocalTimeError, match="does not exist"):
        resolve_event_time(datetime(2026, 3, 29, 1, 30), LONDON)  # noqa: DTZ001


def test_skipped_time_is_not_reported_as_ambiguous() -> None:
    """Regression: a skipped hour also has two differing folds.

    An earlier implementation checked ambiguity first, so an hour that never happened
    was reported as one that happened twice -- the opposite diagnosis, which would have
    sent the user to pick between two occurrences of a nonexistent time.
    """
    skipped = datetime(2026, 3, 29, 1, 30)  # noqa: DTZ001
    assert is_nonexistent(skipped, LONDON)
    assert not is_ambiguous(skipped, LONDON)


# ---------------------------------------------------------------------------
# Travel
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-09")
def test_travel_preserves_the_local_time_that_was_experienced() -> None:
    """A 7am dose in Tokyo stays a 7am dose after flying home to London."""
    in_tokyo = resolve_event_time(datetime(2026, 6, 1, 7, 0), TOKYO)  # noqa: DTZ001

    assert in_tokyo.local_time.hour == 7
    assert in_tokyo.utc_offset_minutes == 540
    assert in_tokyo.occurred_at == datetime(2026, 5, 31, 22, 0, tzinfo=UTC)

    # Reading the same record while in London: the instant is unchanged, the recorded
    # local time still says 7am, and the zone explains why those differ.
    assert in_tokyo.occurred_at.astimezone(UTC).hour == 22
    assert in_tokyo.timezone == TOKYO


@pytest.mark.safety("SAFE-09")
def test_two_events_in_different_zones_order_by_instant() -> None:
    """Comparison must use the instant -- local times are not comparable across zones."""
    tokyo_morning = resolve_event_time(datetime(2026, 6, 1, 7, 0), TOKYO)  # noqa: DTZ001
    london_morning = resolve_event_time(datetime(2026, 6, 1, 7, 0), LONDON)  # noqa: DTZ001

    assert tokyo_morning.local_time == london_morning.local_time
    assert tokyo_morning.occurred_at < london_morning.occurred_at


def test_from_instant_round_trips_a_provider_timestamp() -> None:
    instant = datetime(2026, 7, 15, 6, 8, tzinfo=UTC)
    resolved = from_instant(instant, LONDON)

    assert resolved.occurred_at == instant
    assert resolved.local_time == datetime(2026, 7, 15, 7, 8)  # noqa: DTZ001
    assert resolved.utc_offset_minutes == 60


def test_from_instant_requires_an_aware_datetime() -> None:
    with pytest.raises(ValueError, match="must be timezone-aware"):
        from_instant(datetime(2026, 7, 15, 6, 8), LONDON)  # noqa: DTZ001


# ---------------------------------------------------------------------------
# Consistency
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-09")
@pytest.mark.parametrize(
    ("local", "zone"),
    [
        (datetime(2026, 1, 15, 7, 8), LONDON),  # noqa: DTZ001
        (datetime(2026, 7, 15, 7, 8), LONDON),  # noqa: DTZ001
        (datetime(2026, 6, 1, 7, 0), TOKYO),  # noqa: DTZ001
        (datetime(2026, 11, 5, 23, 30), NEW_YORK),  # noqa: DTZ001
    ],
)
def test_resolved_times_are_internally_consistent(local: datetime, zone: str) -> None:
    assert verify_consistency(resolve_event_time(local, zone))


def test_inconsistent_stored_fields_are_detected() -> None:
    """A row written with a mismatched offset is a data-quality finding, not a repair."""
    broken = EventTime(
        occurred_at=datetime(2026, 1, 15, 7, 8, tzinfo=UTC),
        local_time=datetime(2026, 1, 15, 9, 8),  # noqa: DTZ001 -- claims +2h
        timezone=LONDON,
        utc_offset_minutes=0,  # but records +0
    )
    assert not verify_consistency(broken)


def test_stored_offset_survives_a_tz_database_disagreement() -> None:
    """The stored offset is authoritative; divergence is reported, not corrected.

    If a zone's historical rules were revised, recomputing would silently shift a
    recorded dose. This flags the divergence instead.
    """
    as_captured = EventTime(
        occurred_at=datetime(2026, 7, 15, 6, 8, tzinfo=UTC),
        local_time=datetime(2026, 7, 15, 6, 8),  # noqa: DTZ001
        timezone=LONDON,
        utc_offset_minutes=0,  # as if captured before a rule change
    )
    assert verify_consistency(as_captured)  # internally coherent
    assert not offset_matches_tz_database(as_captured)  # but disagrees with today's rules


def test_offset_matches_tz_database_for_a_freshly_resolved_time() -> None:
    assert offset_matches_tz_database(resolve_event_time(datetime(2026, 7, 15, 7, 8), LONDON))  # noqa: DTZ001


# ---------------------------------------------------------------------------
# EventTime invariants
# ---------------------------------------------------------------------------


def test_event_time_rejects_a_naive_instant() -> None:
    with pytest.raises(ValueError, match="must be timezone-aware and in UTC"):
        EventTime(
            occurred_at=datetime(2026, 1, 1, 0, 0),  # noqa: DTZ001
            local_time=datetime(2026, 1, 1, 0, 0),  # noqa: DTZ001
            timezone=LONDON,
            utc_offset_minutes=0,
        )


def test_event_time_rejects_an_aware_local_time() -> None:
    with pytest.raises(ValueError, match="must be naive"):
        EventTime(
            occurred_at=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            local_time=datetime(2026, 1, 1, 0, 0, tzinfo=UTC),
            timezone=LONDON,
            utc_offset_minutes=0,
        )
