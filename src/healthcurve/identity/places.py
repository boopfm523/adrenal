"""Turn a place the owner names into an IANA zone, without a network call.

"I'm in Chicago" has to become ``America/Chicago`` locally: a geocoding service would
mean telling a third party where the owner is, which is exactly what the privacy model
(hc-p1u.2) exists to prevent.

Two sources, deterministic and offline:

* The tz database itself. Most zone names *are* city names, so ``Chicago``,
  ``New York`` and ``Tokyo`` resolve by matching the city segment of a canonical zone.
* A small alias table for places whose zone is named after a different city --
  ``Baltimore`` is ``America/New_York`` -- and for countries said in conversation.

Anything unmatched resolves to nothing, and the caller falls back to treating the
message as ordinary text. That failure direction matters: "I'm in pain" must never be
read as a place, and it is only safe to look for a place at all because an unrecognised
one changes nothing.

The alias table is meant to be extended when the owner says a place it misses. It does
not try to be a gazetteer; the IANA name always works, and every reply echoes the
resolved zone with its local time so a wrong match is visible immediately.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from functools import cache
from typing import Final
from zoneinfo import ZoneInfo, available_timezones

#: Only zones under a geographic prefix are indexed by city name. The legacy regions
#: (``US/``, ``Brazil/``...) and single-word aliases (``Japan``, ``Zulu``) are links
#: kept for compatibility; indexing them would answer "Eastern" or "Universal" to a
#: question about a place, and echo a deprecated name back as the resolved zone.
_CANONICAL_PREFIXES: Final[frozenset[str]] = frozenset(
    {
        "Africa",
        "America",
        "Antarctica",
        "Arctic",
        "Asia",
        "Atlantic",
        "Australia",
        "Europe",
        "Indian",
        "Pacific",
    }
)

#: Places whose zone is named after somewhere else, plus countries and the few US
#: states that lie wholly in one zone. States that span zones (Texas, Florida) are
#: deliberately absent: there is no correct answer to give, so the owner is asked for
#: a city instead of being handed a coin flip.
_ALIASES: Final[dict[str, str]] = {
    # United States -- eastern
    "baltimore": "America/New_York",
    "boston": "America/New_York",
    "philadelphia": "America/New_York",
    "philly": "America/New_York",
    "washington dc": "America/New_York",
    "washington d c": "America/New_York",
    "dc": "America/New_York",
    "atlanta": "America/New_York",
    "miami": "America/New_York",
    "orlando": "America/New_York",
    "tampa": "America/New_York",
    "charlotte": "America/New_York",
    "raleigh": "America/New_York",
    "pittsburgh": "America/New_York",
    "cleveland": "America/New_York",
    "columbus": "America/New_York",
    "cincinnati": "America/New_York",
    "nyc": "America/New_York",
    "new york city": "America/New_York",
    "manhattan": "America/New_York",
    "brooklyn": "America/New_York",
    "newark": "America/New_York",
    "providence": "America/New_York",
    "richmond": "America/New_York",
    "maryland": "America/New_York",
    # United States -- central
    "dallas": "America/Chicago",
    "houston": "America/Chicago",
    "austin": "America/Chicago",
    "san antonio": "America/Chicago",
    "new orleans": "America/Chicago",
    "nashville": "America/Chicago",
    "memphis": "America/Chicago",
    "milwaukee": "America/Chicago",
    "minneapolis": "America/Chicago",
    "st louis": "America/Chicago",
    "saint louis": "America/Chicago",
    "kansas city": "America/Chicago",
    "omaha": "America/Chicago",
    "oklahoma city": "America/Chicago",
    # United States -- mountain and pacific
    "salt lake city": "America/Denver",
    "albuquerque": "America/Denver",
    "boulder": "America/Denver",
    "colorado springs": "America/Denver",
    "las vegas": "America/Los_Angeles",
    "san francisco": "America/Los_Angeles",
    "sf": "America/Los_Angeles",
    "san diego": "America/Los_Angeles",
    "san jose": "America/Los_Angeles",
    "sacramento": "America/Los_Angeles",
    "seattle": "America/Los_Angeles",
    "portland": "America/Los_Angeles",
    "la": "America/Los_Angeles",
    "california": "America/Los_Angeles",
    "hawaii": "Pacific/Honolulu",
    "alaska": "America/Anchorage",
    # Canada
    "ottawa": "America/Toronto",
    "montreal": "America/Toronto",
    "calgary": "America/Edmonton",
    "quebec": "America/Toronto",
    # United Kingdom and Ireland
    "uk": "Europe/London",
    "england": "Europe/London",
    "scotland": "Europe/London",
    "wales": "Europe/London",
    "manchester": "Europe/London",
    "edinburgh": "Europe/London",
    "glasgow": "Europe/London",
    "birmingham": "Europe/London",
    "cambridge": "Europe/London",
    "oxford": "Europe/London",
    "ireland": "Europe/Dublin",
    # Continental Europe
    "france": "Europe/Paris",
    "germany": "Europe/Berlin",
    "munich": "Europe/Berlin",
    "frankfurt": "Europe/Berlin",
    "hamburg": "Europe/Berlin",
    "cologne": "Europe/Berlin",
    "spain": "Europe/Madrid",
    "barcelona": "Europe/Madrid",
    "valencia": "Europe/Madrid",
    "italy": "Europe/Rome",
    "milan": "Europe/Rome",
    "florence": "Europe/Rome",
    "venice": "Europe/Rome",
    "netherlands": "Europe/Amsterdam",
    "holland": "Europe/Amsterdam",
    "rotterdam": "Europe/Amsterdam",
    "belgium": "Europe/Brussels",
    "switzerland": "Europe/Zurich",
    "geneva": "Europe/Zurich",
    "basel": "Europe/Zurich",
    "austria": "Europe/Vienna",
    "portugal": "Europe/Lisbon",
    "porto": "Europe/Lisbon",
    "greece": "Europe/Athens",
    "poland": "Europe/Warsaw",
    "krakow": "Europe/Warsaw",
    "denmark": "Europe/Copenhagen",
    "sweden": "Europe/Stockholm",
    "norway": "Europe/Oslo",
    "finland": "Europe/Helsinki",
    "czech republic": "Europe/Prague",
    "czechia": "Europe/Prague",
    "hungary": "Europe/Budapest",
    # Rest of world
    "japan": "Asia/Tokyo",
    "osaka": "Asia/Tokyo",
    "kyoto": "Asia/Tokyo",
    "china": "Asia/Shanghai",
    "beijing": "Asia/Shanghai",
    "shenzhen": "Asia/Shanghai",
    "india": "Asia/Kolkata",
    "mumbai": "Asia/Kolkata",
    "bombay": "Asia/Kolkata",
    "delhi": "Asia/Kolkata",
    "new delhi": "Asia/Kolkata",
    "bangalore": "Asia/Kolkata",
    "bengaluru": "Asia/Kolkata",
    "hyderabad": "Asia/Kolkata",
    "chennai": "Asia/Kolkata",
    "israel": "Asia/Jerusalem",
    "tel aviv": "Asia/Jerusalem",
    "south korea": "Asia/Seoul",
    "korea": "Asia/Seoul",
    "thailand": "Asia/Bangkok",
    "vietnam": "Asia/Ho_Chi_Minh",
    "saigon": "Asia/Ho_Chi_Minh",
    "philippines": "Asia/Manila",
    "indonesia": "Asia/Jakarta",
    "bali": "Asia/Makassar",
    "melbourne": "Australia/Melbourne",
    "canberra": "Australia/Sydney",
    "new zealand": "Pacific/Auckland",
    "wellington": "Pacific/Auckland",
    "christchurch": "Pacific/Auckland",
    "mexico city": "America/Mexico_City",
    "brazil": "America/Sao_Paulo",
    "rio": "America/Sao_Paulo",
    "rio de janeiro": "America/Sao_Paulo",
    "argentina": "America/Argentina/Buenos_Aires",
    "south africa": "Africa/Johannesburg",
    "cape town": "Africa/Johannesburg",
    "egypt": "Africa/Cairo",
    "iceland": "Atlantic/Reykjavik",
    "cuba": "America/Havana",
    "iran": "Asia/Tehran",
    "libya": "Africa/Tripoli",
    "kenya": "Africa/Nairobi",
    "nigeria": "Africa/Lagos",
    "abu dhabi": "Asia/Dubai",
    "uae": "Asia/Dubai",
    "turkey": "Europe/Istanbul",
}

_PUNCTUATION: Final = re.compile(r"[.,!?;:'\u2019\"()]+")
_WHITESPACE: Final = re.compile(r"[\s_\-]+")


class AmbiguousPlaceError(ValueError):
    """A place name matches zones that genuinely differ.

    Carries the candidates so the caller can ask rather than choose. Not raised for
    the tz database's deprecated links, which name one zone twice.
    """

    def __init__(self, place: str, candidates: list[str]) -> None:
        super().__init__(f"{place!r} matches more than one timezone: {', '.join(candidates)}")
        self.place = place
        self.candidates = candidates


def normalize(place: str) -> str:
    """Fold a spoken place name to its lookup key."""
    return _WHITESPACE.sub(" ", _PUNCTUATION.sub("", place)).strip().lower()


@cache
def _city_index() -> dict[str, tuple[str, ...]]:
    """City segment of every canonical zone, to the zones carrying it.

    Cached because it walks the whole tz database, and the database does not change
    while the process runs.
    """
    index: dict[str, list[str]] = {}
    for zone in available_timezones():
        region, _, remainder = zone.partition("/")
        if not remainder or region not in _CANONICAL_PREFIXES:
            continue
        index.setdefault(normalize(remainder.rsplit("/", 1)[-1]), []).append(zone)
    return {city: tuple(sorted(zones)) for city, zones in index.items()}


def _preferred(candidates: tuple[str, ...], *, at: datetime) -> str | None:
    """Collapse candidates that are the same zone under two names.

    Every collision in today's tz database is a deprecated link beside its canonical
    name -- ``America/Indianapolis`` for ``America/Indiana/Indianapolis``. They agree
    on the offset, so the deeper (canonical) name is chosen. Candidates that really
    disagree return ``None``, and the caller asks.
    """
    offsets = {ZoneInfo(zone).utcoffset(at) for zone in candidates}
    if len(offsets) > 1:
        return None
    return max(candidates, key=lambda zone: (zone.count("/"), zone))


def _is_canonical_zone(name: str) -> bool:
    """A full zone name under a geographic prefix, as opposed to a legacy link.

    ``Japan`` and ``Universal`` are real entries in the tz database. Accepting them
    verbatim would echo a deprecated name back as the owner's recorded zone, and
    ``Universal`` is not a place anyone is in.
    """
    region, separator, _ = name.partition("/")
    return bool(separator) and region in _CANONICAL_PREFIXES and name in available_timezones()


def resolve(place: str, *, at: datetime | None = None) -> str | None:
    """Return the IANA zone for a place, or ``None`` if it names nowhere known.

    Accepts a canonical IANA name as-is, so ``/tz America/Denver`` always works even
    where the alias table has never heard of.

    Raises :class:`AmbiguousPlaceError` only when candidate zones genuinely differ.
    """
    at = at or datetime.now(UTC)
    raw = place.strip()
    if not raw:
        return None

    if _is_canonical_zone(raw):
        return raw
    if raw.upper() == "UTC":
        return "UTC"

    key = normalize(raw)
    if key in _ALIASES:
        return _ALIASES[key]

    candidates = _city_index().get(key)
    if candidates is None:
        return None
    if len(candidates) == 1:
        return candidates[0]

    chosen = _preferred(candidates, at=at)
    if chosen is None:
        raise AmbiguousPlaceError(raw, list(candidates))
    return chosen
