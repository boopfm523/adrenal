"""Deterministic analysis helpers on synthetic data (ADR-0036)."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from typing import Any

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.orm import sessionmaker

from healthcurve.analysis.tools import AnalysisAccess, execute_analysis_tool
from tests.integration import test_analytics_views as views_database

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

postgres = views_database.postgres
owner_engine = views_database.owner_engine
analyst_engine = views_database.analyst_engine
seeded = views_database.seeded


@pytest.fixture(scope="module")
def ai_engine(postgres: Any) -> Iterator[Engine]:
    engine = create_engine(
        postgres.get_connection_url().replace(
            f"healthcurve:{views_database.OWNER_PASSWORD}@",
            f"healthcurve_ai:{views_database.AI_PASSWORD}@",
        )
    )
    yield engine
    engine.dispose()


@pytest.fixture(scope="module")
def access(owner_engine: Engine, analyst_engine: Engine, ai_engine: Engine) -> AnalysisAccess:
    with owner_engine.connect() as conn:
        owner_id = conn.scalar(
            text("SELECT id FROM identity.owner WHERE email LIKE 'analytics-views-%'")
        )
    assert isinstance(owner_id, uuid.UUID)
    return AnalysisAccess(
        engine=analyst_engine,
        owner_id=owner_id,
        timezone=views_database.ZONE,
        model_session_factory=sessionmaker(ai_engine),
    )


def _data(access: AnalysisAccess, tool: str, arguments: dict[str, Any]) -> dict[str, Any]:
    output = execute_analysis_tool(access, tool, arguments)
    assert output.ok, (output.error_code, output.error_message)
    assert output.data is not None
    return output.data


MARCH = {"date_from": "2026-03-01", "date_to": "2026-03-31"}


def test_clock_time_stats_average_across_midnight(access: AnalysisAccess) -> None:
    bedtime = _data(access, "clock_time_stats", {"source": "bedtime", **MARCH})
    assert bedtime["days_with_values"] == 2
    assert bedtime["summary"]["count"] == 2
    assert bedtime["summary"]["mean_clock"] == "00:00"
    assert (bedtime["summary"]["earliest_clock"], bedtime["summary"]["latest_clock"]) == (
        "23:30",
        "00:30",
    )
    wake = _data(access, "clock_time_stats", {"source": "wake", **MARCH})
    assert wake["summary"]["mean_clock"] == "07:00"


def test_clock_time_stats_dose_sources_and_filters(access: AnalysisAccess) -> None:
    first = _data(access, "clock_time_stats", {"source": "first_dose_of_day", **MARCH})
    assert first["summary"]["count"] == 2
    every = _data(access, "clock_time_stats", {"source": "dose", "medication": "HYDRO", **MARCH})
    assert every["summary"]["count"] == 3
    assert every["filters"] == {"medication": "HYDRO"}
    none = _data(access, "clock_time_stats", {"source": "dose", "dose_category": "stress", **MARCH})
    assert none["summary"]["count"] == 0
    assert none["summary"]["mean_clock"] is None


def test_event_window_stats_split_samples_before_and_after(access: AnalysisAccess) -> None:
    symptom = _data(
        access,
        "event_window_stats",
        {
            "event_source": "symptom",
            "metrics": ["heart_rate", "stress"],
            "minutes_before": 180,
            "minutes_after": 60,
            **MARCH,
        },
    )
    assert symptom["events_total"] == 1
    heart = symptom["summary"]["heart_rate"]
    assert (heart["events_with_before"], heart["events_with_after"]) == (1, 0)
    assert heart["mean_before"] == "70"
    assert heart["mean_after"] is None
    assert symptom["summary"]["stress"]["events_with_before"] == 0
    event = symptom["per_event"][0]
    assert event["label"] == "synthetic fatigue (severity 4)"
    assert event["metrics"]["heart_rate"]["before"]["sample_count"] == 1
    assert event["metrics"]["heart_rate"]["after"] == {
        "sample_count": 0,
        "average": None,
        "minimum": None,
        "maximum": None,
    }
    assert "not causation" in symptom["caution"]

    wake = _data(
        access,
        "event_window_stats",
        {"event_source": "wake", "minutes_after": 60, "include_per_event": False, **MARCH},
    )
    assert wake["events_total"] == 2
    assert wake["summary"]["heart_rate"]["events_with_after"] == 1
    assert wake["per_event"] == []


def test_modeled_exposure_is_labeled_and_uses_the_default_model(access: AnalysisAccess) -> None:
    exposure = _data(
        access, "modeled_exposure", {"date": "2026-03-08", "local_times": ["08:00", "12:00"]}
    )
    assert exposure["model_id"] == "hc-mixed-route-free-v4"
    assert [point["local_time"] for point in exposure["points"]] == ["08:00", "12:00"]
    assert all(point["modeled_free_cortisol_nmol_l"] is not None for point in exposure["points"])
    assert "not a measurement" in exposure["label"]
    assert exposure["day_summary"]["maximum"] is not None
