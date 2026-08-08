"""Synthetic health data for tests, CI, and demos.

Plan section 14: *use synthetic health data in CI and demos*. Nothing here describes a
real person. Every generated record carries :data:`SYNTHETIC_MARKER`, which
:mod:`tests.test_fixture_hygiene` checks, so a real record accidentally pasted into a
fixture fails the build instead of silently entering the repository (SAFE-29).

Generation is deterministic from a seed: the same seed gives the same data, so a test
failure is reproducible and a fixture change shows up as a real diff.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

#: Stamped on every synthetic record. Real data must never carry it, and fixtures
#: must never lack it.
SYNTHETIC_MARKER: Final = "SYNTHETIC-DO-NOT-USE-REAL-DATA"

#: Deliberately not real product names -- these are the shapes the domain needs, not
#: a prescribing reference.
MEDICATIONS: Final[tuple[tuple[str, str, str], ...]] = (
    ("hydrocortisone", "tablet", "mg"),
    ("fludrocortisone", "tablet", "mg"),
    ("hydrocortisone sodium succinate", "injection", "mg"),
)

SYMPTOMS: Final[tuple[str, ...]] = (
    "fatigue",
    "nausea",
    "dizziness",
    "headache",
    "muscle weakness",
    "salt craving",
    "low mood",
)

TRIGGERS: Final[tuple[str, ...]] = (
    "viral illness",
    "dental procedure",
    "vomiting",
    "strenuous exercise",
    "travel",
)


@dataclass(frozen=True, slots=True)
class SyntheticDose:
    """An actual dose taken -- a recorded fact, never a plan record (SAFE-03)."""

    medication: str
    amount: Decimal  # Decimal, never float (ADR-0001)
    unit: str
    route: str
    occurred_at_utc: datetime
    local_time: datetime
    timezone: str
    utc_offset_minutes: int
    category: str  # scheduled | late | replacement | stress | taper | emergency
    marker: str = SYNTHETIC_MARKER


@dataclass(frozen=True, slots=True)
class SyntheticSymptom:
    name: str
    severity: int  # 0-10, an explicitly defined scale
    occurred_at_utc: datetime
    timezone: str
    marker: str = SYNTHETIC_MARKER


@dataclass(frozen=True, slots=True)
class SyntheticEpisode:
    trigger: str
    started_at_utc: datetime
    ended_at_utc: datetime | None
    doses: list[SyntheticDose] = field(default_factory=list)
    symptoms: list[SyntheticSymptom] = field(default_factory=list)
    marker: str = SYNTHETIC_MARKER


@dataclass(frozen=True, slots=True)
class SyntheticRecord:
    doses: list[SyntheticDose]
    symptoms: list[SyntheticSymptom]
    episodes: list[SyntheticEpisode]
    timezone: str
    marker: str = SYNTHETIC_MARKER


#: A plausible three-times-daily replacement schedule, as local times.
DEFAULT_SLOTS: Final[tuple[tuple[time, str], ...]] = (
    (time(7, 0), "10"),
    (time(12, 30), "5"),
    (time(17, 0), "2.5"),
)


def generate_record(
    *,
    seed: int = 0,
    days: int = 14,
    start: date | None = None,
    timezone: str = "Europe/London",
    include_episode: bool = True,
) -> SyntheticRecord:
    """Build a deterministic synthetic record.

    The default timezone is one that observes DST, so fixtures exercise the
    offset-preservation rules (SAFE-09) rather than living comfortably in UTC.
    """
    rng = random.Random(seed)  # noqa: S311 -- fixtures, not cryptography
    tz = ZoneInfo(timezone)
    start = start or date(2026, 3, 22)  # spans the European DST change on 2026-03-29

    doses: list[SyntheticDose] = []
    symptoms: list[SyntheticSymptom] = []

    for offset in range(days):
        day = start + timedelta(days=offset)
        for slot_time, amount in DEFAULT_SLOTS:
            # Real adherence is not perfect: vary timing, and occasionally skip.
            # A skipped dose produces *no record at all* -- never a zero (SAFE-10).
            if rng.random() < 0.06:
                continue
            drift = timedelta(minutes=rng.randint(-20, 75))
            local = datetime.combine(day, slot_time, tzinfo=tz) + drift
            offset_delta = local.utcoffset() or timedelta(0)
            doses.append(
                SyntheticDose(
                    medication="hydrocortisone",
                    amount=Decimal(amount),
                    unit="mg",
                    route="oral",
                    occurred_at_utc=local.astimezone(UTC),
                    local_time=local,
                    timezone=timezone,
                    utc_offset_minutes=int(offset_delta.total_seconds() // 60),
                    category="scheduled" if drift < timedelta(minutes=30) else "late",
                )
            )

        for _ in range(rng.randint(0, 2)):
            local = datetime.combine(day, time(rng.randint(7, 21), 0), tzinfo=tz)
            symptoms.append(
                SyntheticSymptom(
                    name=rng.choice(SYMPTOMS),
                    severity=rng.randint(1, 7),
                    occurred_at_utc=local.astimezone(UTC),
                    timezone=timezone,
                )
            )

    episodes: list[SyntheticEpisode] = []
    if include_episode and days >= 5:
        episodes.append(_generate_episode(rng, start + timedelta(days=days // 2), tz, timezone))

    return SyntheticRecord(
        doses=doses,
        symptoms=symptoms,
        episodes=episodes,
        timezone=timezone,
    )


def _generate_episode(
    rng: random.Random,
    day: date,
    tz: ZoneInfo,
    timezone: str,
) -> SyntheticEpisode:
    """A stress/up-dose episode spanning two days, with extra doses and symptoms."""
    started = datetime.combine(day, time(6, 30), tzinfo=tz)
    ended = started + timedelta(days=1, hours=8)

    stress_doses = [
        SyntheticDose(
            medication="hydrocortisone",
            amount=Decimal("20"),
            unit="mg",
            route="oral",
            occurred_at_utc=(started + timedelta(hours=h)).astimezone(UTC),
            local_time=started + timedelta(hours=h),
            timezone=timezone,
            utc_offset_minutes=int(
                ((started + timedelta(hours=h)).utcoffset() or timedelta(0)).total_seconds() // 60
            ),
            category="stress",
        )
        for h in (0, 6, 12, 24)
    ]
    episode_symptoms = [
        SyntheticSymptom(
            name=rng.choice(SYMPTOMS),
            severity=rng.randint(5, 9),
            occurred_at_utc=(started + timedelta(hours=h)).astimezone(UTC),
            timezone=timezone,
        )
        for h in (1, 8, 20)
    ]
    return SyntheticEpisode(
        trigger=rng.choice(TRIGGERS),
        started_at_utc=started.astimezone(UTC),
        ended_at_utc=ended.astimezone(UTC),
        doses=stress_doses,
        symptoms=episode_symptoms,
    )
