"""Telling the bot where you are, in the words you would actually use.

The risk this file guards is asymmetric. Failing to recognise "I'm in Chicago" costs a
retry; mistaking "I'm in a lot of pain" for a journey silently re-times every entry
that follows, and the owner has no reason to look.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
from sqlalchemy.orm import Session, sessionmaker

from healthcurve.identity import timezones
from healthcurve.identity.models import Owner, TimezoneStaySource
from healthcurve.integrations.telegram import handlers
from tests.fixtures.identity_sqlite import identity_engine

HOME = "America/New_York"
NOW = datetime(2026, 9, 23, 18, 0, tzinfo=UTC)  # 14:00 in New York, 13:00 in Chicago


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
        password_hash="synthetic-not-a-real-hash",
        default_timezone=HOME,
    )
    session.add(row)
    session.flush()
    return row


@pytest.mark.parametrize(
    "message",
    [
        "I'm in Chicago",
        "I am in Chicago",
        "im in chicago",
        "I'm in Chicago now",
        "I'm now in Chicago",
        "I've just landed in Chicago",
        "just landed in Chicago",
        "arrived in Chicago",
        "I'm in Chicago.",
    ],
)
def test_the_phrasings_someone_would_actually_use_are_recognised(
    session: Session, owner: Owner, message: str
) -> None:
    reply = handlers._handle_conversational_shortcut(  # pyright: ignore[reportPrivateUsage]
        session,
        owner,
        message,
        message_id=None,
        client=None,
        limiter=None,
        model_policy=None,
        now=NOW,
        chat_id=4242,
    )

    assert reply is not None
    assert "America/Chicago" in reply.text
    assert timezones.current_zone(session, owner, now=NOW) == "America/Chicago"


def test_going_back_names_the_place_rather_than_assuming_home(
    session: Session, owner: Owner
) -> None:
    """ "back in Baltimore" is a destination, even when it is also where home is."""
    handlers._handle_conversational_shortcut(  # pyright: ignore[reportPrivateUsage]
        session,
        owner,
        "I'm in Chicago",
        message_id=None,
        client=None,
        limiter=None,
        model_policy=None,
        now=NOW,
        chat_id=4242,
    )
    reply = handlers._handle_conversational_shortcut(  # pyright: ignore[reportPrivateUsage]
        session,
        owner,
        "back in Baltimore",
        message_id=None,
        client=None,
        limiter=None,
        model_policy=None,
        now=NOW,
        chat_id=4242,
    )

    assert reply is not None
    assert "America/New_York" in reply.text
    assert timezones.current_zone(session, owner, now=NOW) == "America/New_York"


@pytest.mark.parametrize(
    "message",
    [
        "I'm in pain",
        "I am in a lot of pain",
        "I'm in bed",
        "I'm in trouble",
        "I've just landed in bed",
        "I took 15mg hydrocortisone at 08:00",
        "I'm in Narnia",
    ],
)
def test_a_sentence_that_is_not_about_travel_is_left_for_extraction(message: str) -> None:
    """The safety property: no match means the message reaches the normal pipeline."""
    assert handlers._spoken_timezone_change(message) is None  # pyright: ignore[reportPrivateUsage]
    assert not handlers._is_conversational_shortcut(message)  # pyright: ignore[reportPrivateUsage]


def test_the_reply_shows_the_zone_its_abbreviation_and_the_local_time(
    session: Session, owner: Owner
) -> None:
    """A wrong guess has to be visible before the next dose is recorded against it."""
    reply = handlers._change_timezone(  # pyright: ignore[reportPrivateUsage]
        session, owner, "Chicago", now=NOW
    )

    assert "America/Chicago" in reply.text
    assert "CDT" in reply.text
    assert "13:00" in reply.text
    assert HOME in reply.text  # the home zone is unchanged and said so


def test_restating_the_same_place_reports_that_nothing_changed(
    session: Session, owner: Owner
) -> None:
    handlers._change_timezone(  # pyright: ignore[reportPrivateUsage]
        session, owner, "Chicago", now=NOW
    )
    reply = handlers._change_timezone(  # pyright: ignore[reportPrivateUsage]
        session, owner, "Chicago", now=NOW
    )

    assert "already" in reply.text.lower()
    assert len(timezones.history(session, owner)) == 1


def test_an_unknown_place_records_nothing_and_says_so(session: Session, owner: Owner) -> None:
    reply = handlers._change_timezone(  # pyright: ignore[reportPrivateUsage]
        session, owner, "Narnia", now=NOW
    )

    assert "Nothing changed" in reply.text
    assert timezones.history(session, owner) == []
    assert timezones.current_zone(session, owner, now=NOW) == HOME


def test_tz_with_no_argument_reports_the_current_and_home_zones(
    session: Session, owner: Owner
) -> None:
    reply = handlers._cmd_tz(  # pyright: ignore[reportPrivateUsage]
        session, owner, "", now=NOW
    )

    assert "America/New_York" in reply.text
    assert "No travel recorded" in reply.text


def test_tz_lists_recent_stays_once_there_are_any(session: Session, owner: Owner) -> None:
    # Held deliberately: SQLite re-reads a datetime naive and the identity map is
    # weak, so without a live reference this would fail on the stand-in alone.
    stay = timezones.record_stay(
        session, owner, "America/Chicago", source=TimezoneStaySource.TELEGRAM, started_at=NOW
    )
    session.flush()
    assert stay is not None

    reply = handlers._cmd_tz(  # pyright: ignore[reportPrivateUsage]
        session, owner, "", now=NOW
    )

    assert "America/Chicago" in reply.text
    assert "Recent changes:" in reply.text
    assert "telegram" in reply.text


def test_tz_accepts_an_iana_name_directly(session: Session, owner: Owner) -> None:
    reply = handlers._cmd_tz(  # pyright: ignore[reportPrivateUsage]
        session, owner, "Asia/Tokyo", now=NOW
    )

    assert "Asia/Tokyo" in reply.text
    assert timezones.current_zone(session, owner, now=NOW) == "Asia/Tokyo"


def test_tz_is_a_documented_command(session: Session, owner: Owner) -> None:
    """The help drift gate checks the manifest; this checks the in-chat help."""
    assert "tz" in handlers.SUPPORTED_TELEGRAM_COMMANDS
    assert "/tz" in handlers.HELP_TEXT
