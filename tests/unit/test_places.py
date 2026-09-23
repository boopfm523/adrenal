"""Offline place-to-zone resolution.

Two properties matter more than coverage of any particular city. A place that resolves
must resolve to the *right* zone, and a phrase that is not a place must resolve to
nothing -- because the caller treats "nothing" as permission to hand the message to
extraction, and treats a match as permission to change how every later entry is timed.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from healthcurve.identity import places

NOW = datetime(2026, 9, 23, 12, 0, tzinfo=UTC)


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Chicago", "America/Chicago"),
        ("chicago", "America/Chicago"),
        ("New York", "America/New_York"),
        ("new_york", "America/New_York"),
        ("Denver", "America/Denver"),
        ("Tokyo", "Asia/Tokyo"),
        ("London", "Europe/London"),
        ("Los Angeles", "America/Los_Angeles"),
    ],
)
def test_a_city_that_names_a_zone_resolves_from_the_tz_database(spoken: str, expected: str) -> None:
    assert places.resolve(spoken, at=NOW) == expected


@pytest.mark.parametrize(
    ("spoken", "expected"),
    [
        ("Baltimore", "America/New_York"),
        ("Boston", "America/New_York"),
        ("Seattle", "America/Los_Angeles"),
        ("Austin", "America/Chicago"),
        ("Japan", "Asia/Tokyo"),
        ("Ireland", "Europe/Dublin"),
        ("Hawaii", "Pacific/Honolulu"),
    ],
)
def test_a_city_whose_zone_is_named_elsewhere_resolves_from_the_alias_table(
    spoken: str, expected: str
) -> None:
    assert places.resolve(spoken, at=NOW) == expected


def test_an_iana_name_is_accepted_unchanged() -> None:
    """The escape hatch has to work even where the alias table has never heard of."""
    assert places.resolve("America/Argentina/Buenos_Aires", at=NOW) == (
        "America/Argentina/Buenos_Aires"
    )


@pytest.mark.parametrize(
    "spoken",
    ["pain", "bed", "a lot of pain", "trouble", "the kitchen", "", "   ", "Narnia"],
)
def test_something_that_is_not_a_place_resolves_to_nothing(spoken: str) -> None:
    """The safety property. A non-place must fall through to ordinary extraction."""
    assert places.resolve(spoken, at=NOW) is None


@pytest.mark.parametrize("spoken", ["Chicago.", "Chicago!", "chicago,", "  Chicago  ", "St. Louis"])
def test_ordinary_punctuation_and_spacing_do_not_defeat_a_match(spoken: str) -> None:
    assert places.resolve(spoken, at=NOW) is not None


def test_deprecated_links_collapse_to_the_canonical_zone_rather_than_asking() -> None:
    """``America/Indianapolis`` is the same place as ``America/Indiana/Indianapolis``.

    Reporting that as an ambiguity would ask the owner to choose between two spellings
    of one answer.
    """
    assert places.resolve("Indianapolis", at=NOW) == "America/Indiana/Indianapolis"


def test_legacy_region_aliases_are_not_treated_as_places() -> None:
    """``US/Eastern`` exists in the tz database; "Eastern" is not a place to be in."""
    assert places.resolve("Eastern", at=NOW) is None
    assert places.resolve("Universal", at=NOW) is None


def test_every_alias_points_at_a_zone_that_exists() -> None:
    """A typo in the table would otherwise surface as a crash mid-conversation."""
    aliases = places._ALIASES  # pyright: ignore[reportPrivateUsage]
    for alias, zone in aliases.items():
        assert places.resolve(zone, at=NOW) == zone, alias


def test_every_alias_key_is_already_normalized() -> None:
    """A key that normalization can never produce is a key that can never match."""
    aliases = places._ALIASES  # pyright: ignore[reportPrivateUsage]
    for alias in aliases:
        assert places.normalize(alias) == alias
