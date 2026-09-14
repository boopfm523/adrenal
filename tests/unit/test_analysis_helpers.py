"""Argument contracts for deterministic analysis helpers (ADR-0036). No database."""

from __future__ import annotations

import datetime as dt
from typing import Any

import pytest
from pydantic import ValidationError

from healthcurve.analysis.helpers import (
    ClockTimeStatsArguments,
    EventWindowStatsArguments,
    ModeledExposureArguments,
    _like_pattern,  # pyright: ignore[reportPrivateUsage]
)
from healthcurve.analysis.tools import AnalysisAccess, execute_analysis_tool, tool_definitions


def test_date_ranges_are_ordered_and_bounded() -> None:
    ClockTimeStatsArguments(
        source="bedtime", date_from=dt.date(2026, 1, 1), date_to=dt.date(2026, 12, 31)
    )
    with pytest.raises(ValidationError, match="on or after"):
        ClockTimeStatsArguments(
            source="wake", date_from=dt.date(2026, 2, 1), date_to=dt.date(2026, 1, 1)
        )
    with pytest.raises(ValidationError, match="cannot exceed"):
        ClockTimeStatsArguments(
            source="wake", date_from=dt.date(2025, 1, 1), date_to=dt.date(2026, 1, 2)
        )


@pytest.mark.parametrize(
    "overrides",
    [
        {"minutes_before": 4},
        {"minutes_after": 721},
        {"metrics": []},
        {"metrics": ["steps"]},
        {"event_source": "diary"},
        {"owner_id": "not-allowed"},
    ],
)
def test_event_window_arguments_reject_out_of_contract_values(overrides: dict[str, Any]) -> None:
    arguments: dict[str, Any] = {
        "event_source": "symptom",
        "date_from": "2026-09-01",
        "date_to": "2026-09-14",
        **overrides,
    }
    with pytest.raises(ValidationError):
        EventWindowStatsArguments.model_validate(arguments)


def test_modeled_exposure_accepts_only_clock_times() -> None:
    ModeledExposureArguments(date=dt.date(2026, 9, 1), local_times=["07:30", "23:59"])
    with pytest.raises(ValidationError):
        ModeledExposureArguments(date=dt.date(2026, 9, 1), local_times=["24:00"])


def test_like_filters_escape_wildcards() -> None:
    assert _like_pattern("hydro_100%") == "%hydro\\_100\\%%"
    assert _like_pattern(None) is None


def test_catalog_exposes_helpers_and_hides_unconfigured_modeled_exposure() -> None:
    names = {item["function"]["name"] for item in tool_definitions()}
    assert names == {
        "describe_data",
        "run_query",
        "clock_time_stats",
        "event_window_stats",
        "modeled_exposure",
    }
    limited = {item["function"]["name"] for item in tool_definitions(AnalysisAccess(engine=None))}
    assert "modeled_exposure" not in limited

    unavailable = execute_analysis_tool(
        AnalysisAccess(engine=None), "modeled_exposure", {"date": "2026-09-01"}
    )
    assert not unavailable.ok
    assert unavailable.error_code == "modeled_exposure_not_configured"

    unconfigured = execute_analysis_tool(
        AnalysisAccess(engine=None),
        "clock_time_stats",
        {"source": "bedtime", "date_from": "2026-09-01", "date_to": "2026-09-14"},
    )
    assert not unconfigured.ok
    assert unconfigured.error_code == "analysis_not_configured"
