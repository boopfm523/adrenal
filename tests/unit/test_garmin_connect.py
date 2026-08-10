"""Synthetic contract and security tests for the unofficial Garmin adapter."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

import pytest

from healthcurve.integrations.garmin.connect_client import (
    GarminProviderError,
    PythonGarminReadClient,
    validate_token_store_path,
)
from healthcurve.integrations.garmin.connect_mapping import map_activities, map_day
from healthcurve.integrations.garmin.connect_sync import fetch_window
from healthcurve.integrations.garmin.models import GarminMetricType


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1_000)


def _daily_sleep(
    *,
    start_utc: datetime,
    end_utc: datetime,
    start_local_as_utc: datetime,
    timezone: str,
    duration: int | None = None,
) -> dict[str, Any]:
    dto: dict[str, Any] = {
        "sleepStartTimestampGMT": _milliseconds(start_utc),
        "sleepEndTimestampGMT": _milliseconds(end_utc),
        "sleepStartTimestampLocal": _milliseconds(start_local_as_utc),
        "timeZoneId": timezone,
        "awakeCount": 3,
        "sleepScores": {"overall": {"value": 84}},
    }
    if duration is not None:
        dto["sleepTimeSeconds"] = duration
    return {"dailySleepDTO": dto}


def test_daily_metrics_preserve_missingness_and_sleep_provenance() -> None:
    start = datetime(2026, 1, 9, 4, 0, tzinfo=UTC)
    end = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)
    mapped = map_day(
        day=date(2026, 1, 9),
        stats={"totalSteps": 8_765, "restingHeartRate": 58},
        sleep=_daily_sleep(
            start_utc=start,
            end_utc=end,
            start_local_as_utc=datetime(2026, 1, 8, 23, 0, tzinfo=UTC),
            timezone="America/New_York",
        ),
        timezone="America/New_York",
    )

    values = {metric.metric_type: metric for metric in mapped.metrics}
    assert values[GarminMetricType.STEPS].unit == "steps"
    assert values[GarminMetricType.RESTING_HEART_RATE].unit == "bpm"
    assert GarminMetricType.STRESS not in values
    assert mapped.capabilities["stress"] == "unavailable"
    assert mapped.sleep is not None
    assert mapped.sleep.duration_seconds == 8 * 60 * 60
    assert mapped.sleep.duration_source == "calculated_from_bounds"
    assert mapped.sleep.awakenings == 3
    assert mapped.sleep.score == 84


def test_sleep_crossing_dst_uses_source_zone_and_elapsed_duration() -> None:
    mapped = map_day(
        day=date(2026, 3, 8),
        stats={},
        sleep=_daily_sleep(
            start_utc=datetime(2026, 3, 8, 6, 30, tzinfo=UTC),
            end_utc=datetime(2026, 3, 8, 8, 30, tzinfo=UTC),
            start_local_as_utc=datetime(2026, 3, 8, 1, 30, tzinfo=UTC),
            timezone="America/New_York",
        ),
        timezone="Europe/London",
    )

    assert mapped.sleep is not None
    assert mapped.sleep.event_time.timezone == "America/New_York"
    assert mapped.sleep.event_time.local_time.isoformat() == "2026-03-08T01:30:00"
    assert mapped.sleep.event_time.utc_offset_minutes == -300
    assert mapped.sleep.duration_seconds == 7_200


def test_activity_contract_covers_miles_unknown_types_and_missing_distance() -> None:
    activities, warnings = map_activities(
        [
            {
                "activityId": 1,
                "activityType": {"typeKey": "walking"},
                "activityName": "Synthetic walk",
                "startTimeGMT": "2026-07-01T12:00:00Z",
                "startTimeLocal": "2026-07-01T13:00:00",
                "timeZoneId": "Europe/London",
                "elapsedDuration": 1_800,
                "distance": 1_609.344,
            },
            {
                "activityId": 2,
                "activityType": {"typeKey": "cycling"},
                "activityName": "Synthetic ride",
                "startTimeGMT": "2026-07-01T14:00:00Z",
                "startTimeLocal": "2026-07-01T15:00:00",
                "timeZoneId": "Europe/London",
                "elapsedDuration": 3_600,
            },
            {
                "activityId": 3,
                "activityType": {"typeKey": "Snow Shoe / Other"},
                "startTimeGMT": "2026-07-01T16:00:00Z",
                "startTimeLocal": "2026-07-01T17:00:00",
                "timeZoneId": "Europe/London",
                "duration": 900,
                "distance": 0,
            },
            {
                "activityId": 4,
                "activityType": {"typeKey": "running"},
                "startTimeGMT": "2026-07-01T18:00:00Z",
                "startTimeLocal": "2026-07-01T19:00:00",
                "elapsedDuration": 0,
            },
        ],
        timezone="America/New_York",
    )

    assert len(activities) == 3
    assert activities[0].distance_miles == 1
    assert activities[0].event_time.timezone == "Europe/London"
    assert activities[1].distance_miles is None
    assert activities[2].sport == "snow_shoe_other"
    assert "activity_duration_invalid" in warnings


class _SyntheticClient:
    def __init__(self) -> None:
        self.logged_in = False

    def login(self) -> None:
        self.logged_in = True

    def get_stats(self, day: str) -> dict[str, Any]:
        return {"totalSteps": 100}

    def get_sleep_data(self, day: str) -> dict[str, Any]:
        return {}

    def get_activities_by_date(self, start: str, end: str) -> list[dict[str, Any]]:
        return []

    def logout(self) -> None:
        return None


def test_fetch_window_rate_limits_every_provider_read() -> None:
    client = _SyntheticClient()
    clock = [10.0]
    pauses: list[float] = []

    def monotonic() -> float:
        return clock[0]

    def pause(seconds: float) -> None:
        pauses.append(seconds)
        clock[0] += seconds

    fetched = fetch_window(
        client,
        start_date=date(2026, 1, 1),
        end_date=date(2026, 1, 1),
        timezone="UTC",
        minimum_call_interval_s=0.5,
        monotonic=monotonic,
        pause=pause,
    )

    assert client.logged_in
    assert pauses == [0.5, 0.5]
    assert fetched.capabilities["activities"] == "unavailable"


def test_token_store_rejects_repository_and_insecure_permissions(tmp_path: Path) -> None:
    with pytest.raises(GarminProviderError, match=r"^garmin_token_path_in_repository$"):
        validate_token_store_path(Path.cwd() / "private-garmin-token")

    token_store = tmp_path / "garmin"
    token_store.mkdir(mode=0o755)
    with pytest.raises(GarminProviderError, match=r"^garmin_token_store_permissions$"):
        PythonGarminReadClient(
            email=None,
            password=None,
            token_store=token_store,
        ).login()


def test_provider_exception_text_is_never_returned(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    token_store = tmp_path / "garmin"
    token_store.mkdir(mode=0o700)

    class FakeGarmin:
        def __init__(self, *_args: Any, **_kwargs: Any) -> None:
            pass

        def login(self, path: str) -> tuple[None, None]:
            token_file = Path(path) / "garmin_tokens.json"
            token_file.write_text("{}")
            os.chmod(token_file, 0o600)
            return None, None

        def get_stats(self, _day: str) -> dict[str, Any]:
            raise RuntimeError("synthetic-secret-provider-message")

    monkeypatch.setattr("healthcurve.integrations.garmin.connect_client.Garmin", FakeGarmin)
    client = PythonGarminReadClient(
        email="synthetic@example.invalid",
        password="synthetic-password",
        token_store=token_store,
    )
    client.login()
    with pytest.raises(GarminProviderError, match=r"^garmin_client_failed$") as caught:
        client.get_stats("2026-01-01")
    assert "synthetic-secret" not in str(caught.value)
