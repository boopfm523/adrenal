"""Synthetic contract and security tests for the unofficial Garmin adapter."""

from __future__ import annotations

import os
from datetime import UTC, date, datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

import pytest

from healthcurve.integrations.garmin.connect_client import (
    GarminProviderError,
    PythonGarminReadClient,
    validate_token_store_path,
)
from healthcurve.integrations.garmin.connect_intraday import map_intraday_day
from healthcurve.integrations.garmin.connect_mapping import map_activities, map_day
from healthcurve.integrations.garmin.connect_sync import fetch_window
from healthcurve.integrations.garmin.models import GarminMetricType
from healthcurve.integrations.garmin.presentation import measurement_summary


def _milliseconds(value: datetime) -> int:
    return int(value.timestamp() * 1_000)


def _daily_sleep(
    *,
    start_utc: datetime,
    end_utc: datetime,
    start_local_as_utc: datetime,
    timezone: str,
    duration: int | None = None,
    sleep_levels: list[dict[str, Any]] | None = None,
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
    payload: dict[str, Any] = {"dailySleepDTO": dto}
    if sleep_levels is not None:
        payload["sleepLevels"] = sleep_levels
    return payload


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


def test_sleep_maps_only_explicit_bounded_awake_intervals() -> None:
    start = datetime(2026, 1, 9, 4, 0, tzinfo=UTC)
    end = datetime(2026, 1, 9, 12, 0, tzinfo=UTC)
    mapped = map_day(
        day=date(2026, 1, 9),
        stats={},
        sleep=_daily_sleep(
            start_utc=start,
            end_utc=end,
            start_local_as_utc=datetime(2026, 1, 8, 23, 0, tzinfo=UTC),
            timezone="America/New_York",
            sleep_levels=[
                {
                    "startGMT": "2026-01-09T04:00:00Z",
                    "endGMT": "2026-01-09T06:00:00Z",
                    "activityLevel": 1,
                },
                {
                    "startGMT": "2026-01-09T06:00:00Z",
                    "endGMT": "2026-01-09T06:12:00Z",
                    "activityLevel": 3,
                },
                {
                    "startGMT": "2026-01-09T06:12:00Z",
                    "endGMT": "2026-01-09T12:00:00Z",
                    "sleepLevel": "light",
                },
                {
                    "startGMT": "2026-01-09T03:59:00Z",
                    "endGMT": "2026-01-09T04:01:00Z",
                    "activityLevel": 3,
                },
            ],
        ),
        timezone="America/New_York",
    )

    assert mapped.sleep is not None
    assert mapped.sleep.stage_count == 3
    assert [
        (interval.stage, interval.started_at, interval.ended_at)
        for interval in mapped.sleep.stage_intervals
    ] == [("awake", datetime(2026, 1, 9, 6, tzinfo=UTC), datetime(2026, 1, 9, 6, 12, tzinfo=UTC))]
    assert "sleep_stage_bounds_invalid" in mapped.warnings


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
                "locationName": "Synthetic City, ST",
                "startLatitude": 39.2904,
                "startLongitude": -76.6122,
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
    assert activities[0].environment == "outdoor"
    assert activities[0].location_name == "Synthetic City, ST"
    assert activities[0].latitude == Decimal("39.3")
    assert activities[0].longitude == Decimal("-76.6")
    assert activities[1].distance_miles is None
    assert activities[1].environment == "unknown"
    assert activities[2].sport == "snow_shoe_other"
    assert "activity_duration_invalid" in warnings


def test_activity_environment_blocks_indoor_weather_and_preserves_missingness() -> None:
    activities, warnings = map_activities(
        [
            {
                "activityId": 10,
                "activityType": {"typeKey": "treadmill_running"},
                "activityName": "Synthetic treadmill",
                "startTimeGMT": "2026-07-01T12:00:00Z",
                "startTimeLocal": "2026-07-01T08:00:00",
                "elapsedDuration": 1_200,
                "startLatitude": 39.29,
                "startLongitude": -76.61,
            },
            {
                "activityId": 11,
                "activityType": {"typeKey": "walking"},
                "activityName": "Synthetic location-missing walk",
                "startTimeGMT": "2026-07-01T14:00:00Z",
                "startTimeLocal": "2026-07-01T10:00:00",
                "elapsedDuration": 1_200,
                "startLatitude": "not-a-coordinate",
                "startLongitude": -76.61,
            },
        ],
        timezone="America/New_York",
    )

    assert activities[0].environment == "indoor"
    assert activities[0].latitude == Decimal("39.3")
    assert activities[1].environment == "outdoor"
    assert activities[1].latitude is None
    assert activities[1].longitude is None
    assert "activity_location_invalid" in warnings


class _SyntheticClient:
    def __init__(self) -> None:
        self.logged_in = False

    def login(self) -> None:
        self.logged_in = True

    def get_stats(self, day: str) -> dict[str, Any]:
        return {"totalSteps": 100}

    def get_sleep_data(self, day: str) -> dict[str, Any]:
        return {}

    def get_heart_rates(self, day: str) -> dict[str, Any]:
        return {}

    def get_stress_data(self, day: str) -> dict[str, Any]:
        return {}

    def get_respiration_data(self, day: str) -> dict[str, Any]:
        return {}

    def get_hrv_data(self, day: str) -> dict[str, Any]:
        return {}

    def get_steps_data(self, day: str) -> list[dict[str, Any]]:
        return []

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
    assert pauses == [0.5] * 7
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


def test_read_only_adapter_exposes_approved_intraday_methods(
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

        def get_heart_rates(self, day: str) -> dict[str, Any]:
            return {"method": "heart_rate", "day": day}

        def get_stress_data(self, day: str) -> dict[str, Any]:
            return {"method": "stress", "day": day}

        def get_respiration_data(self, day: str) -> dict[str, Any]:
            return {"method": "respiration", "day": day}

        def get_hrv_data(self, day: str) -> None:
            return None

        def get_steps_data(self, day: str) -> list[dict[str, Any]]:
            return [{"method": "steps", "day": day}]

    monkeypatch.setattr("healthcurve.integrations.garmin.connect_client.Garmin", FakeGarmin)
    client = PythonGarminReadClient(email=None, password=None, token_store=token_store)
    client.login()

    assert client.get_heart_rates("2026-01-09")["method"] == "heart_rate"
    assert client.get_stress_data("2026-01-09")["method"] == "stress"
    assert client.get_respiration_data("2026-01-09")["method"] == "respiration"
    assert client.get_hrv_data("2026-01-09") == {}
    assert client.get_steps_data("2026-01-09")[0]["method"] == "steps"


def test_intraday_contract_maps_timestamped_series_and_preserves_missingness() -> None:
    start = _milliseconds(datetime(2026, 1, 9, 5, 0, tzinfo=UTC))
    mapped = map_intraday_day(
        day=date(2026, 1, 9),
        heart_rate={
            "heartRateValueDescriptors": [
                {"index": 1, "key": "heartrate"},
                {"index": 0, "key": "timestamp"},
            ],
            "heartRateValues": [[start, 72], [start + 120_000, None]],
        },
        stress={
            "stressValueDescriptorsDTOList": [
                {"index": 0, "key": "timestamp"},
                {"index": 1, "key": "stressLevel"},
            ],
            # Negative provider sentinels are missing; zero is a valid stress score.
            "stressValuesArray": [
                [start, -1],
                [start + 180_000, 0],
                [start + 360_000, 44],
            ],
        },
        respiration={
            "respirationValueDescriptorsDTOList": [
                {"index": 0, "key": "timestamp"},
                {"index": 1, "key": "respiration"},
            ],
            "respirationValuesArray": [[start, -2], [start + 120_000, 14.5]],
            "avgWakingRespirationValue": 14.2,
            "avgSleepRespirationValue": 12.7,
            "lowestRespirationValue": 10.1,
            "highestRespirationValue": 18.9,
        },
        hrv={
            "hrvSummary": {"lastNightAvg": 45},
            "hrvReadings": [
                {"readingTimeGMT": "2026-01-09T05:00:00Z", "hrvValue": 40},
                {"readingTimeGMT": "2026-01-09T05:05:00Z", "hrvValue": 42},
            ],
        },
        steps=[
            {"startGMT": "2026-01-09T05:00:00Z", "endGMT": "2026-01-09T05:15:00Z", "steps": 10},
            {"startGMT": "2026-01-09T05:15:00Z", "endGMT": "2026-01-09T05:30:00Z", "steps": 20},
            {"startGMT": "2026-01-09T05:30:00Z", "endGMT": "2026-01-09T05:45:00Z", "steps": 0},
            {"startGMT": "2026-01-09T05:45:00Z", "endGMT": "2026-01-09T06:00:00Z", "steps": 5},
        ],
        timezone="America/New_York",
    )

    values = [(item.metric_type, item.value) for item in mapped.observations]
    assert values == [
        (GarminMetricType.HEART_RATE, 72),
        (GarminMetricType.HRV, 40),
        (GarminMetricType.STEPS, Decimal("35")),
        (GarminMetricType.RESPIRATION_RATE, Decimal("14.5")),
        (GarminMetricType.STRESS, 0),
        (GarminMetricType.HRV, 42),
        (GarminMetricType.STRESS, 44),
    ]
    assert all(item.event_time.timezone == "America/New_York" for item in mapped.observations)
    assert all(item.provider_id.startswith("intraday:") for item in mapped.observations)
    assert [(item.field_name, item.value, item.unit) for item in mapped.aggregates] == [
        ("lastNightAvg", Decimal("45"), "ms"),
        ("avgWakingRespirationValue", Decimal("14.2"), "breaths/min"),
        ("avgSleepRespirationValue", Decimal("12.7"), "breaths/min"),
        ("lowestRespirationValue", Decimal("10.1"), "breaths/min"),
        ("highestRespirationValue", Decimal("18.9"), "breaths/min"),
    ]
    assert all(item.event_time.local_time.date() == date(2026, 1, 9) for item in mapped.aggregates)
    assert [item.sample_interval_seconds for item in mapped.observations] == [
        None,
        None,
        3600,
        None,
        None,
        300,
        180,
    ]
    assert mapped.capabilities == {
        "intraday_heart_rate": "available",
        "intraday_stress": "available",
        "intraday_respiration_rate": "available",
        "intraday_hrv": "available",
        "intraday_steps": "available",
        "hrv_daily_average": "unsupported",
        "hrv_nightly_average": "available",
        "respiration_waking_average": "available",
        "respiration_sleep_average": "available",
        "respiration_daily_low": "available",
        "respiration_daily_high": "available",
    }
    assert "intraday_heart_rate_missing_or_invalid" in mapped.warnings
    assert "intraday_stress_missing_or_invalid" in mapped.warnings
    assert "intraday_respiration_rate_missing_or_invalid" in mapped.warnings


def test_intraday_contract_is_deterministic_and_rejects_ambiguous_duplicates() -> None:
    start = _milliseconds(datetime(2026, 11, 1, 5, 30, tzinfo=UTC))
    heart_rate = {
        "heartRateValueDescriptors": [
            {"index": 0, "key": "timestamp"},
            {"index": 1, "key": "heartrate"},
        ],
        "heartRateValues": [[start, 70], [start, 71]],
    }
    first = map_intraday_day(
        day=date(2026, 11, 1),
        heart_rate=heart_rate,
        stress={},
        respiration={},
        hrv={"hrvSummary": {"lastNightAvg": 44}},
        steps=[],
        timezone="America/New_York",
    )
    second = map_intraday_day(
        day=date(2026, 11, 1),
        heart_rate=heart_rate,
        stress={},
        respiration={},
        hrv={"hrvSummary": {"lastNightAvg": 44}},
        steps=[],
        timezone="America/New_York",
    )

    assert first == second
    assert len(first.observations) == 1
    assert first.observations[0].event_time.utc_offset_minutes == -240
    assert len(first.aggregates) == 1
    assert first.aggregates[0].event_time.utc_offset_minutes == -240
    assert first.aggregates[0].provider_id == "aggregate:2026-11-01:hrv:lastNightAvg"
    assert "intraday_heart_rate_duplicate_timestamp" in first.warnings
    assert first.capabilities["intraday_hrv"] == "unavailable"


def test_intraday_contract_requires_provider_descriptors() -> None:
    mapped = map_intraday_day(
        day=date(2026, 1, 9),
        heart_rate={"heartRateValues": [[1_767_938_400_000, 70]]},
        stress={},
        respiration={
            "avgWakingRespirationValue": -2,
            "highestRespirationValue": 18,
        },
        hrv={"hrvReadings": "not-a-list", "hrvSummary": ["invalid"]},
        steps=[],
        timezone="UTC",
    )

    assert mapped.observations == ()
    assert [(item.field_name, item.value) for item in mapped.aggregates] == [
        ("highestRespirationValue", Decimal(18))
    ]
    assert "intraday_heart_rate_shape_invalid" in mapped.warnings
    assert "intraday_hrv_shape_invalid" in mapped.warnings
    assert "hrv_nightly_average_shape_invalid" in mapped.warnings
    assert "respiration_waking_average_invalid" in mapped.warnings
    assert mapped.capabilities["hrv_daily_average"] == "unsupported"
    assert mapped.capabilities["hrv_nightly_average"] == "unavailable"
    assert mapped.capabilities["respiration_daily_high"] == "available"
    assert mapped.capabilities["respiration_daily_low"] == "unavailable"


def test_measurement_summary_hides_decimal_padding_and_internal_unit() -> None:
    assert (
        measurement_summary(
            GarminMetricType.STRESS,
            "averageStressLevel",
            Decimal("31.0000"),
            "garmin_score",
        )
        == "Stress: 31"
    )
    assert (
        measurement_summary(
            GarminMetricType.RESPIRATION_RATE,
            "avgWakingRespirationValue",
            Decimal("14.2000"),
            "breaths/min",
        )
        == "Average waking respiration: 14.2 breaths/min"
    )
    assert (
        measurement_summary(
            GarminMetricType.HRV,
            "lastNightAvg",
            Decimal("35.0000"),
            "ms",
        )
        == "Nightly average HRV: 35 ms"
    )
    assert (
        measurement_summary(
            GarminMetricType.STEPS,
            "totalSteps",
            Decimal("0.0000"),
            "steps",
        )
        == "Steps: 0 steps"
    )
