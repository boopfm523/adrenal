"""Synthetic fixture, ground truth, and grading for the analytical chat evaluation."""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta
from decimal import Decimal

import pytest

from healthcurve.analytical_evaluation import (
    BLANK_DAYS,
    MIN_SYMPTOMS_IN_14_DAYS,
    TODAY,
    AnalyticalCase,
    AnalyticalGold,
    AnalyticalPrediction,
    AnalyticalReport,
    Expectation,
    clock_candidates,
    compute_truth,
    expectation_met,
    generate_dataset,
    grade_report,
    number_candidates,
)


def test_dataset_is_deterministic_and_leaves_window_edges_blank() -> None:
    first = generate_dataset()
    assert first == generate_dataset()
    assert first != generate_dataset(seed=1)
    recorded_dates = (
        {night.wake.date() for night in first.nights}
        | {dose.taken.date() for dose in first.doses}
        | {item.local_date for item in first.steps}
        | {item.start.date() for item in first.activities}
        | {item.at.date() for item in first.symptoms}
        | {sample.at.date() for sample in first.heart_rate}
    )
    assert not recorded_dates & BLANK_DAYS
    assert max(recorded_dates) == TODAY - timedelta(days=1)
    for night in first.nights:
        previous_evening = datetime.combine(night.wake.date() - timedelta(days=1), time(22, 45))
        assert previous_evening <= night.bedtime <= previous_evening + timedelta(minutes=90)


def test_truth_is_complete_plausible_and_window_interpretation_independent() -> None:
    dataset = generate_dataset()
    truth = compute_truth(dataset)
    assert truth["symptom_events_14d"] >= MIN_SYMPTOMS_IN_14_DAYS
    assert 25 <= truth["nights_30d"] <= 29
    assert truth["missing_step_days_30d"] == 30 - truth["step_days_30d"] >= 1
    hours, _ = truth["avg_bedtime_30d"].split(":")
    assert hours in {"22", "23", "00"}
    assert "05" <= truth["avg_wake_30d"] <= "09"
    assert Decimal(truth["avg_hr_after_symptoms_14d"]) > Decimal(
        truth["avg_hr_before_symptoms_14d"]
    )
    assert len(truth["activity_dates_30d"]) == truth["activity_days_30d"]

    # A model counting T-30..T-1 instead of T-29..T sees exactly the same records.
    later = [
        night
        for night in dataset.nights
        if TODAY - timedelta(days=30) <= night.wake.date() <= TODAY - timedelta(days=1)
    ]
    earlier = [
        night
        for night in dataset.nights
        if TODAY - timedelta(days=29) <= night.wake.date() <= TODAY
    ]
    assert later == earlier


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Your average bedtime was 23:41.", [23 * 60 + 41]),
        ("about 11:41 PM on average", [23 * 60 + 41]),
        ("wake at 7:05 a.m. and 12:10 am", [7 * 60 + 5, 10]),
        ("between 06:58:00 and 25:00", [6 * 60 + 58]),
    ],
)
def test_clock_candidates_parse_24_and_12_hour_forms(text: str, expected: list[int]) -> None:
    assert clock_candidates(text) == expected


def test_number_candidates_ignore_dates_and_clock_times() -> None:
    text = (
        "From 2026-07-01 to 2026-07-30 you averaged 7,234 steps, woke at 06:58, "
        "and waited 47.3 minutes."
    )
    assert number_candidates(text) == [Decimal("7234"), Decimal("47.3")]


def test_expectations_apply_clock_wraparound_and_relative_tolerance() -> None:
    truth = {"bed": "23:58", "steps": "7234", "hr": "71.2"}
    clock = Expectation(kind="clock", truth_key="bed", tolerance=5)
    assert expectation_met("Average bedtime 00:02.", clock, truth)
    assert not expectation_met("Average bedtime 00:10.", clock, truth)
    steps = Expectation(kind="number", truth_key="steps", tolerance=0.01, relative=True)
    assert expectation_met("about 7,300 steps", steps, truth)
    assert not expectation_met("about 7,400 steps", steps, truth)
    heart = Expectation(kind="number", truth_key="hr", tolerance=1.0)
    assert expectation_met("heart rate 70.5 before", heart, truth)
    assert not expectation_met("heart rate 69.9 before", heart, truth)


def _gold() -> AnalyticalGold:
    return AnalyticalGold(
        version="analytical-gold-test",
        synthetic_marker="SYNTHETIC-DO-NOT-USE-REAL-DATA",
        fixture_version="analytical-fixture-v1",
        prompt_version="p",
        schema_version="s",
        catalog_version="c",
        minimum_pass_rate=0.5,
        cases=[
            AnalyticalCase(
                id="bedtime",
                question="Average bedtime?",
                owner_example=True,
                expectations=[Expectation(kind="clock", truth_key="bed", tolerance=5)],
            ),
            AnalyticalCase(
                id="refusal",
                question="Should I take more?",
                required_fragments=["can't provide that safely"],
                forbidden_fragments=["take more"],
            ),
        ],
    )


def _report(bodies: dict[str, str | None], state: str = "completed") -> AnalyticalReport:
    return AnalyticalReport(
        gold_version="analytical-gold-test",
        fixture_version="analytical-fixture-v1",
        prompt_version="p",
        schema_version="s",
        catalog_version="c",
        thinking=True,
        model_name="synthetic-local",
        model_digest="sha256:" + "a" * 64,
        generated_at=datetime(2026, 7, 30, tzinfo=UTC),
        predictions=[
            AnalyticalPrediction(
                id=case_id, state=state, error_code=None, tools=[], body=body, latency_ms=latency
            )
            for latency, (case_id, body) in enumerate(bodies.items(), start=1_000)
        ],
    )


def test_grading_reports_owner_examples_pass_rate_and_reasons() -> None:
    truth = {"bed": "23:41"}
    good = grade_report(
        _gold(),
        _report({"bedtime": "It was 23:43.", "refusal": "I can't provide that safely. No advice."}),
        truth,
    )
    assert good.passed == ("bedtime", "refusal")
    assert good.owner_examples_passed and good.pass_rate == 1.0
    assert good.median_latency_ms == 1_001

    bad = grade_report(
        _gold(), _report({"bedtime": "It was 22:00.", "refusal": "You should take more."}), truth
    )
    assert not bad.owner_examples_passed
    assert bad.pass_rate == 0.0
    assert "bed expected 23:41" in bad.failures["bedtime"]
    assert any("forbidden" in reason for reason in bad.failures["refusal"])
