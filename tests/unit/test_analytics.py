import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Any, cast

from healthcurve.analytics.service import (
    DayInput,
    EpisodeInput,
    SymptomInput,
    TimingInput,
    summarize,
)


def test_deterministic_summary_matches_independent_fixture() -> None:
    started = datetime(2026, 8, 1, 9, tzinfo=UTC)
    result = summarize(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 2),
        timezone="Europe/London",
        days=[
            DayInput(
                day=date(2026, 8, 1),
                planned_total=Decimal("20"),
                actual_total=Decimal("25"),
                recorded_dose_count=2,
                statuses=("on_time", "unplanned"),
            ),
            DayInput(
                day=date(2026, 8, 2),
                planned_total=Decimal("20"),
                actual_total=Decimal("0"),
                recorded_dose_count=0,
                statuses=("missing", "missing"),
            ),
        ],
        episodes=[
            EpisodeInput(started, started + timedelta(minutes=90)),
            EpisodeInput(started + timedelta(days=1), None),
        ],
        symptoms=[
            SymptomInput("Synthetic fatigue", 4),
            SymptomInput("Synthetic fatigue", None),
            SymptomInput("Synthetic nausea", 8),
        ],
    )

    assert result["timezone"] == "Europe/London"
    doses = result["daily_doses"]
    assert isinstance(doses, dict)
    assert doses["sample_count"] == 2
    assert doses["missing_count"] == 1
    assert doses["days_without_approved_plan"] == 0
    assert doses["values"] == [
        {
            "date": date(2026, 8, 1),
            "planned_total": Decimal("20"),
            "actual_total": Decimal("25"),
            "recorded_dose_count": 2,
            "unit": "mg",
            "incompatible_units": False,
        },
        {
            "date": date(2026, 8, 2),
            "planned_total": Decimal("20"),
            "actual_total": None,
            "recorded_dose_count": 0,
            "unit": "mg",
            "incompatible_units": False,
        },
    ]

    timing = result["timing"]
    assert isinstance(timing, dict)
    assert timing["on_time"] == 1
    assert timing["missing_count"] == 2
    assert timing["unplanned"] == 1
    assert "30 minutes" in str(timing["definition"])

    episodes = result["episodes"]
    assert isinstance(episodes, dict)
    assert episodes["count"] == 2
    assert episodes["missing_count"] == 1
    assert episodes["total_duration_minutes"] == Decimal("90")
    assert episodes["average_duration_minutes"] == Decimal("90")

    symptoms = result["symptoms"]
    assert isinstance(symptoms, dict)
    assert symptoms["count"] == 3
    assert symptoms["missing_count"] == 1
    assert symptoms["average_severity"] == Decimal("6")
    assert symptoms["frequency"] == {"Synthetic fatigue": 2, "Synthetic nausea": 1}


def test_empty_summary_distinguishes_missing_from_zero() -> None:
    result = summarize(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 1),
        timezone="UTC",
        days=[DayInput(date(2026, 8, 1), None, Decimal(0), 0, ())],
        episodes=[],
        symptoms=[],
    )
    doses = result["daily_doses"]
    assert isinstance(doses, dict)
    assert doses["values"] == [
        {
            "date": date(2026, 8, 1),
            "planned_total": None,
            "actual_total": None,
            "recorded_dose_count": 0,
            "unit": "mg",
            "incompatible_units": False,
        }
    ]
    assert doses["missing_count"] == 1
    assert doses["days_without_approved_plan"] == 1


def test_incompatible_units_are_not_combined() -> None:
    result = summarize(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 1),
        timezone="UTC",
        days=[
            DayInput(
                date(2026, 8, 1),
                Decimal("11"),
                Decimal("12"),
                2,
                ("on_time", "unplanned"),
                unit=None,
                incompatible_units=True,
            )
        ],
        episodes=[],
        symptoms=[],
    )
    doses = result["daily_doses"]
    assert isinstance(doses, dict)
    value = doses["values"][0]  # type: ignore[index]
    assert value["planned_total"] is None  # type: ignore[index]
    assert value["actual_total"] is None  # type: ignore[index]
    assert value["incompatible_units"] is True  # type: ignore[index]


def test_timing_deviation_excludes_missing_and_unplanned_and_groups_plan_history() -> None:
    earlier = uuid.UUID("11111111-1111-4111-8111-111111111111")
    later = uuid.UUID("22222222-2222-4222-8222-222222222222")
    first_start = datetime(2026, 8, 1, tzinfo=UTC)
    transition = datetime(2026, 8, 8, tzinfo=UTC)
    result = summarize(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 9),
        timezone="America/New_York",
        days=[
            DayInput(
                day=date(2026, 8, 1),
                planned_total=Decimal("20"),
                actual_total=Decimal("25"),
                recorded_dose_count=2,
                statuses=("late", "missing", "unplanned"),
                timings=(
                    TimingInput("late", 45, earlier, "Earlier", first_start, transition),
                    TimingInput("missing", None, earlier, "Earlier", first_start, transition),
                    TimingInput("unplanned", None, earlier, "Earlier", first_start, transition),
                ),
            ),
            DayInput(
                day=date(2026, 8, 9),
                planned_total=Decimal("20"),
                actual_total=Decimal("20"),
                recorded_dose_count=1,
                statuses=("early",),
                timings=(TimingInput("early", -15, later, "Later", transition, None),),
            ),
        ],
        episodes=[],
        symptoms=[],
    )

    timing = result["timing"]
    assert isinstance(timing, dict)
    assert timing["sample_count"] == 4
    assert timing["matched_count"] == 2
    assert timing["total_absolute_deviation_minutes"] == Decimal("60")
    assert timing["average_absolute_deviation_minutes"] == Decimal("30")
    periods = cast(list[dict[str, Any]], timing["plan_periods"])
    assert [period["regimen_version_label"] for period in periods] == ["Earlier", "Later"]
    assert periods[0]["average_absolute_deviation_minutes"] == Decimal("45")
    assert periods[0]["missing_count"] == 1
    assert periods[0]["unplanned"] == 1
    assert periods[1]["average_absolute_deviation_minutes"] == Decimal("15")


def test_wake_timing_uses_signed_observed_wake_to_dose_minutes() -> None:
    result = summarize(
        date_from=date(2026, 8, 1),
        date_to=date(2026, 8, 3),
        timezone="America/New_York",
        days=[
            DayInput(
                day=date(2026, 8, 1),
                planned_total=Decimal("15"),
                actual_total=Decimal("15"),
                recorded_dose_count=1,
                statuses=("on_time",),
                timings=(
                    TimingInput(
                        status="on_time",
                        minutes_from_scheduled=None,
                        timing_mode="wake",
                        minutes_from_wake=17,
                    ),
                ),
            ),
            DayInput(
                day=date(2026, 8, 2),
                planned_total=Decimal("15"),
                actual_total=Decimal("15"),
                recorded_dose_count=1,
                statuses=("on_time",),
                timings=(
                    TimingInput(
                        status="on_time",
                        minutes_from_scheduled=None,
                        timing_mode="wake",
                        minutes_from_wake=-5,
                    ),
                ),
            ),
            DayInput(
                day=date(2026, 8, 3),
                planned_total=Decimal("15"),
                actual_total=Decimal("0"),
                recorded_dose_count=0,
                statuses=("missing",),
                timings=(
                    TimingInput(
                        status="missing",
                        minutes_from_scheduled=None,
                        timing_mode="wake",
                        minutes_from_wake=None,
                    ),
                ),
            ),
        ],
        episodes=[],
        symptoms=[],
    )

    timing = result["timing"]
    assert isinstance(timing, dict)
    assert timing["wake_sample_count"] == 3
    assert timing["wake_matched_count"] == 2
    assert timing["wake_missing_count"] == 1
    assert timing["wake_total_signed_minutes"] == Decimal("12")
    assert timing["wake_average_signed_minutes"] == Decimal("6")
