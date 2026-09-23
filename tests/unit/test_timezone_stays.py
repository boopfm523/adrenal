"""The zone ledger: resolution at an instant, and for a stated wall time.

The case worth protecting is the quiet one. A dose recorded in the wrong zone still
looks like a valid dose -- it is simply an hour from where it happened, which is enough
to move a peak on the cortisol curve.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st
from sqlalchemy.orm import Session, sessionmaker

from healthcurve.identity import timezones
from healthcurve.identity.models import Owner, TimezoneStay, TimezoneStaySource
from tests.fixtures.identity_sqlite import identity_engine

HOME = "America/New_York"
AWAY = "America/Chicago"
FAR = "Asia/Tokyo"


@pytest.fixture
def session() -> Iterator[Session]:
    engine = identity_engine()
    try:
        with sessionmaker(engine)() as opened:
            yield opened
    finally:
        engine.dispose()


@pytest.fixture
def owner(session: Session) -> Owner:
    row = Owner(
        id=uuid.uuid4(),
        email="traveller@example.test",
        password_hash="synthetic-not-a-real-hash",  # pragma: allowlist secret
        default_timezone=HOME,
    )
    session.add(row)
    session.flush()
    return row


def _wall(year: int, month: int, day: int, hour: int, minute: int = 0) -> datetime:
    """A wall-clock reading. Naive is the point: which zone it belongs to is the
    question under test, so attaching one here would assume the answer.
    """
    return datetime(year, month, day, hour, minute)  # noqa: DTZ001


def _utc(value: datetime) -> datetime:
    """SQLite drops the tzinfo that PostgreSQL's timestamptz preserves."""
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _stay(session: Session, owner: Owner, zone: str, started_at: datetime) -> None:
    timezones.record_stay(
        session, owner, zone, source=TimezoneStaySource.TELEGRAM, started_at=started_at
    )
    session.flush()


def test_an_instant_before_the_first_stay_resolves_to_the_home_zone(
    session: Session, owner: Owner
) -> None:
    """The non-destructive guarantee: adding the ledger reinterprets no old event."""
    departure = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    _stay(session, owner, AWAY, departure)

    assert timezones.zone_at(session, owner, departure - timedelta(seconds=1)) == HOME
    assert timezones.zone_at(session, owner, departure) == AWAY


def test_the_stay_in_force_is_the_most_recent_one_that_has_begun(
    session: Session, owner: Owner
) -> None:
    first = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    second = datetime(2026, 9, 10, 12, 0, tzinfo=UTC)
    _stay(session, owner, AWAY, first)
    _stay(session, owner, FAR, second)

    assert timezones.zone_at(session, owner, first + timedelta(days=1)) == AWAY
    assert timezones.zone_at(session, owner, second + timedelta(days=1)) == FAR


def test_restating_the_same_zone_records_nothing(session: Session, owner: Owner) -> None:
    """Saying "I'm in Chicago" twice is a restatement, not a second journey."""
    _stay(session, owner, AWAY, datetime(2026, 9, 20, 12, 0, tzinfo=UTC))

    again = timezones.record_stay(
        session,
        owner,
        AWAY,
        source=TimezoneStaySource.TELEGRAM,
        started_at=datetime(2026, 9, 20, 18, 0, tzinfo=UTC),
    )

    assert again is None
    assert len(timezones.history(session, owner)) == 1


def test_recording_the_home_zone_before_any_travel_records_nothing(
    session: Session, owner: Owner
) -> None:
    assert (
        timezones.record_stay(
            session, owner, HOME, source=TimezoneStaySource.TELEGRAM, started_at=datetime.now(UTC)
        )
        is None
    )


def test_a_correction_in_the_same_instant_replaces_rather_than_duplicates(
    session: Session, owner: Owner
) -> None:
    """The unique constraint forbids two stays at one instant; this must not raise."""
    instant = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
    _stay(session, owner, AWAY, instant)
    _stay(session, owner, FAR, instant)

    stays = timezones.history(session, owner)
    assert len(stays) == 1
    assert stays[0].timezone == FAR


def test_an_unknown_zone_is_rejected_without_recording(session: Session, owner: Owner) -> None:
    with pytest.raises(timezones.UnknownTimezoneError):
        timezones.record_stay(session, owner, "Mars/Olympus", source=TimezoneStaySource.TELEGRAM)

    assert timezones.history(session, owner) == []


def test_recording_a_stay_does_not_alter_an_earlier_one(session: Session, owner: Owner) -> None:
    first = datetime(2026, 9, 1, 12, 0, tzinfo=UTC)
    _stay(session, owner, AWAY, first)
    before = timezones.history(session, owner)[0]
    recorded_id, recorded_zone = before.id, before.timezone

    _stay(session, owner, FAR, datetime(2026, 9, 10, 12, 0, tzinfo=UTC))

    earlier = session.get(TimezoneStay, recorded_id)
    assert earlier is not None
    assert earlier.timezone == recorded_zone
    assert _utc(earlier.started_at) == first


class TestStatedWallTime:
    """`zone_for_local_time` is what makes a backfilled entry land correctly."""

    def test_a_wall_time_inside_the_current_stay_uses_the_current_zone(
        self, session: Session, owner: Owner
    ) -> None:
        _stay(session, owner, AWAY, datetime(2026, 9, 20, 12, 0, tzinfo=UTC))
        now = datetime(2026, 9, 21, 23, 0, tzinfo=UTC)  # 18:00 in Chicago

        assert (
            timezones.zone_for_local_time(session, owner, _wall(2026, 9, 21, 8, 0), now=now) == AWAY
        )

    def test_a_wall_time_before_the_stay_began_uses_the_zone_left_behind(
        self, session: Session, owner: Owner
    ) -> None:
        """ "Took it at 8am", sent after landing, means 8am where the owner then was.

        Landing at 14:00 UTC and reporting an 08:00 dose: 08:00 Chicago would be 13:00
        UTC, which is before the stay began, so the dose belongs to the home zone.
        """
        _stay(session, owner, AWAY, datetime(2026, 9, 20, 14, 0, tzinfo=UTC))
        now = datetime(2026, 9, 20, 15, 0, tzinfo=UTC)

        assert (
            timezones.zone_for_local_time(session, owner, _wall(2026, 9, 20, 8, 0), now=now) == HOME
        )

    def test_a_wall_time_after_the_stay_began_uses_the_new_zone(
        self, session: Session, owner: Owner
    ) -> None:
        _stay(session, owner, AWAY, datetime(2026, 9, 20, 14, 0, tzinfo=UTC))
        now = datetime(2026, 9, 21, 2, 0, tzinfo=UTC)

        assert (
            timezones.zone_for_local_time(session, owner, _wall(2026, 9, 20, 16, 0), now=now)
            == AWAY
        )

    def test_it_falls_back_to_the_home_zone_with_no_stays_at_all(
        self, session: Session, owner: Owner
    ) -> None:
        now = datetime(2026, 9, 21, 12, 0, tzinfo=UTC)
        assert (
            timezones.zone_for_local_time(session, owner, _wall(2026, 9, 21, 8, 0), now=now) == HOME
        )


@settings(max_examples=200, deadline=None)
@given(
    boundary_offset_minutes=st.integers(min_value=-720, max_value=720),
    stated_offset_minutes=st.integers(min_value=-1440, max_value=0),
)
def test_a_stated_wall_time_always_resolves_to_a_zone_that_contains_it(
    boundary_offset_minutes: int, stated_offset_minutes: int
) -> None:
    """Whatever zone is chosen, converting under it must land inside that zone's stay.

    This is the property the two-pass convergence exists to provide: a wall time near a
    stay boundary must not resolve to a zone whose stay does not cover the resulting
    instant. Built as its own engine so Hypothesis controls the whole fixture lifetime.
    """
    engine = identity_engine()
    with sessionmaker(engine)() as session:
        owner = Owner(
            id=uuid.uuid4(),
            email="property@example.test",
            password_hash="synthetic-not-a-real-hash",  # pragma: allowlist secret
            default_timezone=HOME,
        )
        session.add(owner)

        reference = datetime(2026, 9, 20, 12, 0, tzinfo=UTC)
        boundary = reference + timedelta(minutes=boundary_offset_minutes)
        timezones.record_stay(
            session, owner, AWAY, source=TimezoneStaySource.TELEGRAM, started_at=boundary
        )
        session.flush()

        now = reference + timedelta(hours=12)
        stated = (
            (reference + timedelta(minutes=stated_offset_minutes))
            .astimezone(timezones.load_zone(AWAY))
            .replace(tzinfo=None)
        )

        zone = timezones.zone_for_local_time(session, owner, stated, now=now)
        instant = stated.replace(tzinfo=timezones.load_zone(zone), fold=0).astimezone(UTC)

        assert timezones.zone_at(session, owner, instant) == zone

    engine.dispose()
