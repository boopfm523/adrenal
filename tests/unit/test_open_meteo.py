"""Privacy, retry, missingness, and provenance tests for weather enrichment."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import cast
from unittest.mock import MagicMock

import httpx
import pytest
from sqlalchemy.orm import Session

from healthcurve.context.models import ContextEvent, LocationPrecision
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.integrations.garmin.models import GarminActivityEvent
from healthcurve.integrations.weather import open_meteo
from healthcurve.integrations.weather.jobs import (
    enqueue_activity_weather_enrichment,
    make_activity_weather_handler,
    make_weather_handler,
)
from healthcurve.operations.jobs import JobQueueError

OWNER_ID = uuid.UUID("00000000-0000-4000-8000-000000000201")
SOURCE_ID = uuid.UUID("00000000-0000-4000-8000-000000000202")
OBSERVED_AT = datetime(2026, 8, 10, 12, tzinfo=UTC)


def _response(*, current: dict[str, object] | None = None) -> dict[str, object]:
    return {
        "timezone": "America/New_York",
        "current_units": {
            "time": "unixtime",
            "temperature_2m": "°C",
            "relative_humidity_2m": "%",
            "surface_pressure": "hPa",
            "precipitation": "mm",
            "weather_code": "wmo code",
        },
        "current": current
        or {
            "time": int(OBSERVED_AT.timestamp()),
            "temperature_2m": 21.4,
            "relative_humidity_2m": 55,
            "surface_pressure": 1009.2,
            "precipitation": 0,
            "weather_code": 2,
        },
    }


def _historical_response() -> dict[str, object]:
    times = [int(datetime(2026, 8, 10, hour, tzinfo=UTC).timestamp()) for hour in (11, 12, 13, 14)]
    return {
        "timezone": "UTC",
        "hourly_units": {
            "time": "unixtime",
            "temperature_2m": "°C",
            "apparent_temperature": "°C",
            "relative_humidity_2m": "%",
            "precipitation": "mm",
            "weather_code": "wmo code",
            "wind_speed_10m": "km/h",
            "wind_gusts_10m": "km/h",
        },
        "hourly": {
            "time": times,
            "temperature_2m": [25, 30, 32, 31],
            "apparent_temperature": [26, 34, 37, 35],
            "relative_humidity_2m": [50, 60, 70, 65],
            "precipitation": [0, 0, 0.5, 0],
            "weather_code": [1, 2, 2, 3],
            "wind_speed_10m": [5, 10, 15, 10],
            "wind_gusts_10m": [10, 20, 30, 25],
        },
    }


def test_request_discloses_only_rounded_coordinates_time_and_fixed_fields() -> None:
    captured: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_response())

    observation = open_meteo.fetch_current(
        Decimal("40.7"),
        Decimal("-74.0"),
        transport=httpx.MockTransport(provider),
        sleep=lambda _delay: None,
    )

    assert observation.temperature_c == Decimal("21.4")
    assert observation.conditions == "Cloudy"
    assert observation.confidence is None
    assert len(captured) == 1
    request = captured[0]
    assert request.method == "GET"
    assert set(request.url.params) == open_meteo.REQUEST_KEYS
    assert request.url.params["latitude"] == "40.7"
    assert request.url.params["longitude"] == "-74.0"
    serialized = str(request.url)
    for forbidden in ("owner", "draft", "event", "health", str(OWNER_ID), str(SOURCE_ID)):
        assert forbidden not in serialized
    assert request.content == b""


def test_unrounded_coordinates_are_rejected_before_network() -> None:
    called = False

    def provider(_request: httpx.Request) -> httpx.Response:
        nonlocal called
        called = True
        return httpx.Response(200, json=_response())

    with pytest.raises(JobQueueError, match="weather_location_precision_invalid"):
        open_meteo.fetch_current(
            Decimal("40.71"), Decimal("-74.0"), transport=httpx.MockTransport(provider)
        )
    assert called is False


def test_historical_activity_weather_uses_only_coarse_location_and_interval() -> None:
    captured: list[httpx.Request] = []

    def provider(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(200, json=_historical_response())

    started_at = datetime(2026, 8, 10, 12, 15, tzinfo=UTC)
    ended_at = datetime(2026, 8, 10, 13, 20, tzinfo=UTC)
    observation = open_meteo.fetch_historical_interval(
        Decimal("39.3"),
        Decimal("-76.6"),
        started_at=started_at,
        ended_at=ended_at,
        transport=httpx.MockTransport(provider),
        sleep=lambda _delay: None,
    )

    assert observation.temperature_c == Decimal("31")
    assert observation.apparent_temperature_c == Decimal("37")
    assert observation.humidity_percent == Decimal("65")
    assert observation.precipitation_mm == Decimal("0.5")
    assert observation.conditions == "Cloudy"
    assert observation.wind_speed_kph == Decimal("35") / Decimal("3")
    assert observation.wind_gust_kph == Decimal("30")
    request = captured[0]
    assert set(request.url.params) == open_meteo.HISTORICAL_REQUEST_KEYS
    assert request.url.params["latitude"] == "39.3"
    assert request.url.params["longitude"] == "-76.6"
    assert request.url.params["start_date"] == "2026-08-10"
    assert request.url.params["end_date"] == "2026-08-10"


def test_activity_weather_queue_excludes_indoor_and_missing_location() -> None:
    activity = MagicMock(spec=GarminActivityEvent)
    activity.environment = "indoor"
    activity.sport = "treadmill_running"
    activity.location_latitude = Decimal("39.3")
    activity.location_longitude = Decimal("-76.6")
    session = cast(Session, MagicMock(spec=Session))

    assert enqueue_activity_weather_enrichment(session, activity) is None
    activity.environment = "outdoor"
    activity.sport = "walking"
    activity.location_latitude = None
    assert enqueue_activity_weather_enrichment(session, activity) is None


def test_activity_weather_handler_creates_private_linked_context(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    activity = MagicMock(spec=GarminActivityEvent)
    activity.id = SOURCE_ID
    activity.owner_id = OWNER_ID
    activity.environment = "outdoor"
    activity.sport = "walking"
    activity.location_name = "Synthetic City, ST"
    activity.location_latitude = Decimal("39.3")
    activity.location_longitude = Decimal("-76.6")
    activity.occurred_at = OBSERVED_AT
    activity.ended_at = OBSERVED_AT.replace(hour=13)
    activity.timezone = "America/New_York"
    mocked = MagicMock(spec=Session)
    mocked.get.return_value = activity
    mocked.scalar.side_effect = [None, None]
    session = cast(Session, mocked)
    create = MagicMock()
    monkeypatch.setattr("healthcurve.integrations.weather.jobs.events.create_event", create)
    observation = open_meteo.HistoricalWeatherObservation(
        observed_at=OBSERVED_AT,
        interval_ended_at=activity.ended_at,
        timezone="UTC",
        temperature_c=Decimal("30"),
        apparent_temperature_c=Decimal("35"),
        humidity_percent=Decimal("65"),
        precipitation_mm=Decimal("0"),
        conditions="Cloudy",
        wind_speed_kph=Decimal("10"),
        wind_gust_kph=Decimal("20"),
    )

    make_activity_weather_handler(fetch=lambda *_args, **_kwargs: observation)(
        session, {"activity_event_id": str(SOURCE_ID)}
    )

    kwargs = create.call_args.kwargs
    assert kwargs["coarse_location_label"] == "Synthetic City, ST"
    assert kwargs["latitude"] == Decimal("39.3")
    assert kwargs["apparent_temperature"] == Decimal("35")
    assert kwargs["wind_gust_kph"] == Decimal("20")
    assert kwargs["provider_id"] == f"open-meteo:garmin-activity:{SOURCE_ID}"


def test_rate_limit_and_network_retries_are_bounded() -> None:
    statuses = [429, 503, 200]
    delays: list[float] = []

    def provider(_request: httpx.Request) -> httpx.Response:
        status = statuses.pop(0)
        return httpx.Response(status, json=_response())

    observation = open_meteo.fetch_current(
        Decimal("40.7"),
        Decimal("-74.0"),
        transport=httpx.MockTransport(provider),
        sleep=delays.append,
    )

    assert observation.observed_at == OBSERVED_AT
    assert statuses == []
    assert delays == [0.25, 0.5]

    attempts = 0

    def unavailable(request: httpx.Request) -> httpx.Response:
        nonlocal attempts
        attempts += 1
        raise httpx.ReadTimeout("synthetic timeout", request=request)

    with pytest.raises(JobQueueError, match="weather_provider_unavailable"):
        open_meteo.fetch_current(
            Decimal("40.7"),
            Decimal("-74.0"),
            transport=httpx.MockTransport(unavailable),
            sleep=lambda _delay: None,
        )
    assert attempts == 3


def test_missing_or_incompatible_provider_values_never_become_zero() -> None:
    def missing(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=_response(
                current={
                    "time": int(OBSERVED_AT.timestamp()),
                    "temperature_2m": None,
                    "relative_humidity_2m": None,
                    "surface_pressure": None,
                    "precipitation": None,
                    "weather_code": None,
                }
            ),
        )

    with pytest.raises(JobQueueError, match="weather_data_missing"):
        open_meteo.fetch_current(
            Decimal("40.7"),
            Decimal("-74.0"),
            transport=httpx.MockTransport(missing),
            sleep=lambda _delay: None,
        )


def test_job_creates_a_separate_provider_fact_with_full_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = MagicMock(spec=ContextEvent)
    source.id = SOURCE_ID
    source.owner_id = OWNER_ID
    source.latitude = Decimal("40.7")
    source.longitude = Decimal("-74.0")
    source.location_precision = LocationPrecision.COARSE
    source.exact_location_consent = False
    source.coarse_location_label = "Approximate phone location"
    mocked = MagicMock(spec=Session)
    mocked.get.return_value = source
    mocked.scalar.return_value = None
    session = cast(Session, mocked)
    create = MagicMock()
    monkeypatch.setattr(
        "healthcurve.integrations.weather.jobs.events.create_event",
        create,
    )
    observation = open_meteo.WeatherObservation(
        observed_at=OBSERVED_AT,
        timezone="America/New_York",
        temperature_c=Decimal("21.4"),
        pressure_hpa=Decimal("1009.2"),
        humidity_percent=Decimal("55"),
        precipitation_mm=Decimal("0"),
        conditions="Cloudy",
    )

    make_weather_handler(fetch=lambda _lat, _lon: observation)(
        session, {"context_event_id": str(SOURCE_ID)}
    )

    kwargs = create.call_args.kwargs
    assert kwargs["owner_id"] == OWNER_ID
    assert kwargs["source_type"] is SourceType.PROVIDER
    assert kwargs["confirmation_state"] is ConfirmationState.PROVIDER_IMPORTED
    assert kwargs["weather_provider"] == "open-meteo"
    assert kwargs["weather_observed_at"] == OBSERVED_AT
    assert kwargs["weather_confidence"] is None
    assert kwargs["latitude"] == Decimal("40.7")
    assert kwargs["longitude"] == Decimal("-74.0")
    assert kwargs["provider_id"] == f"open-meteo:{SOURCE_ID}"
    assert len(kwargs["weather_observation_id"]) == 64


def test_job_payload_rejects_everything_except_an_opaque_context_id() -> None:
    session = cast(Session, MagicMock(spec=Session))
    handler = make_weather_handler(fetch=lambda _lat, _lon: MagicMock())

    with pytest.raises(JobQueueError, match="weather_job_payload_invalid"):
        handler(
            session,
            {"context_event_id": str(SOURCE_ID), "latitude": "40.7"},
        )


def test_job_refuses_an_exact_location_even_when_values_have_one_decimal() -> None:
    source = MagicMock(spec=ContextEvent)
    source.latitude = Decimal("40.7")
    source.longitude = Decimal("-74.0")
    source.location_precision = LocationPrecision.EXACT
    source.exact_location_consent = True
    mocked = MagicMock(spec=Session)
    mocked.get.return_value = source
    session = cast(Session, mocked)

    with pytest.raises(JobQueueError, match="weather_source_precision_invalid"):
        make_weather_handler(fetch=lambda _lat, _lon: MagicMock())(
            session, {"context_event_id": str(SOURCE_ID)}
        )
