"""Privacy-minimized Open-Meteo current and historical-weather adapter."""

from __future__ import annotations

import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from typing import Final

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from healthcurve.operations.jobs import JobQueueError

PROVIDER: Final = "open-meteo"
ENDPOINT: Final = "https://api.open-meteo.com/v1/forecast"
HISTORICAL_ENDPOINT: Final = "https://historical-forecast-api.open-meteo.com/v1/forecast"
CURRENT_FIELDS: Final = (
    "temperature_2m",
    "relative_humidity_2m",
    "surface_pressure",
    "precipitation",
    "weather_code",
)
REQUEST_KEYS: Final = frozenset({"latitude", "longitude", "current", "timezone", "timeformat"})
HISTORICAL_FIELDS: Final = (
    "temperature_2m",
    "apparent_temperature",
    "relative_humidity_2m",
    "precipitation",
    "weather_code",
    "wind_speed_10m",
    "wind_gusts_10m",
)
HISTORICAL_REQUEST_KEYS: Final = frozenset(
    {"latitude", "longitude", "hourly", "start_date", "end_date", "timezone", "timeformat"}
)
TIMEOUT: Final = httpx.Timeout(connect=5.0, read=10.0, write=5.0, pool=5.0)


class _Current(BaseModel):
    model_config = ConfigDict(extra="ignore")

    time: int
    temperature_2m: Decimal | None = None
    relative_humidity_2m: Decimal | None = Field(default=None, ge=0, le=100)
    surface_pressure: Decimal | None = Field(default=None, gt=0)
    precipitation: Decimal | None = Field(default=None, ge=0)
    weather_code: int | None = None


class _Response(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timezone: str = Field(min_length=1, max_length=64)
    current_units: dict[str, str]
    current: _Current


class _Hourly(BaseModel):
    model_config = ConfigDict(extra="ignore")

    time: list[int]
    temperature_2m: list[Decimal | None]
    apparent_temperature: list[Decimal | None]
    relative_humidity_2m: list[Decimal | None]
    precipitation: list[Decimal | None]
    weather_code: list[int | None]
    wind_speed_10m: list[Decimal | None]
    wind_gusts_10m: list[Decimal | None]


class _HistoricalResponse(BaseModel):
    model_config = ConfigDict(extra="ignore")

    timezone: str = Field(min_length=1, max_length=64)
    hourly_units: dict[str, str]
    hourly: _Hourly


@dataclass(frozen=True, slots=True)
class WeatherObservation:
    observed_at: datetime
    timezone: str
    temperature_c: Decimal | None
    pressure_hpa: Decimal | None
    humidity_percent: Decimal | None
    precipitation_mm: Decimal | None
    conditions: str | None
    confidence: Decimal | None = None


@dataclass(frozen=True, slots=True)
class HistoricalWeatherObservation:
    observed_at: datetime
    interval_ended_at: datetime
    timezone: str
    temperature_c: Decimal | None
    apparent_temperature_c: Decimal | None
    humidity_percent: Decimal | None
    precipitation_mm: Decimal | None
    conditions: str | None
    wind_speed_kph: Decimal | None
    wind_gust_kph: Decimal | None
    confidence: Decimal | None = None


def _rounded_parameter(value: Decimal) -> str:
    if not value.is_finite() or value != value.quantize(Decimal("0.1")):
        raise JobQueueError("weather_location_precision_invalid")
    return f"{value:.1f}"


def request_parameters(latitude: Decimal, longitude: Decimal) -> dict[str, str]:
    """Return the complete, allow-listed provider disclosure."""
    if not Decimal("-90") <= latitude <= Decimal("90"):
        raise JobQueueError("weather_location_invalid")
    if not Decimal("-180") <= longitude <= Decimal("180"):
        raise JobQueueError("weather_location_invalid")
    parameters = {
        "latitude": _rounded_parameter(latitude),
        "longitude": _rounded_parameter(longitude),
        "current": ",".join(CURRENT_FIELDS),
        "timezone": "auto",
        "timeformat": "unixtime",
    }
    if parameters.keys() != REQUEST_KEYS:
        raise JobQueueError("weather_request_fields_invalid")
    return parameters


def historical_request_parameters(
    latitude: Decimal, longitude: Decimal, *, started_at: datetime, ended_at: datetime
) -> dict[str, str]:
    if started_at.utcoffset() is None or ended_at.utcoffset() is None or ended_at <= started_at:
        raise JobQueueError("weather_interval_invalid")
    location = request_parameters(latitude, longitude)
    parameters = {
        "latitude": location["latitude"],
        "longitude": location["longitude"],
        "hourly": ",".join(HISTORICAL_FIELDS),
        "start_date": started_at.astimezone(UTC).date().isoformat(),
        "end_date": ended_at.astimezone(UTC).date().isoformat(),
        "timezone": "UTC",
        "timeformat": "unixtime",
    }
    if parameters.keys() != HISTORICAL_REQUEST_KEYS:
        raise JobQueueError("weather_request_fields_invalid")
    return parameters


def _condition(code: int | None) -> str | None:
    if code is None:
        return None
    if code == 0:
        return "Clear sky"
    if code in {1, 2, 3}:
        return "Cloudy"
    if code in {45, 48}:
        return "Fog"
    if code in {51, 53, 55, 56, 57}:
        return "Drizzle"
    if code in {61, 63, 65, 66, 67, 80, 81, 82}:
        return "Rain"
    if code in {71, 73, 75, 77, 85, 86}:
        return "Snow"
    if code in {95, 96, 99}:
        return "Thunderstorm"
    return "Other weather"


def _parse(response: httpx.Response) -> WeatherObservation:
    try:
        body = _Response.model_validate(response.json())
    except (ValidationError, ValueError) as exc:
        raise JobQueueError("weather_response_invalid") from exc
    current = body.current
    values = (
        current.temperature_2m,
        current.relative_humidity_2m,
        current.surface_pressure,
        current.precipitation,
        current.weather_code,
    )
    if all(value is None for value in values):
        raise JobQueueError("weather_data_missing")
    expected_units = {
        "temperature_2m": "°C",
        "relative_humidity_2m": "%",
        "surface_pressure": "hPa",
        "precipitation": "mm",
    }
    for field, expected in expected_units.items():
        if getattr(current, field) is not None and body.current_units.get(field) != expected:
            raise JobQueueError("weather_units_invalid")
    try:
        observed_at = datetime.fromtimestamp(current.time, tz=UTC)
    except (OverflowError, OSError, ValueError) as exc:
        raise JobQueueError("weather_time_invalid") from exc
    return WeatherObservation(
        observed_at=observed_at,
        timezone=body.timezone,
        temperature_c=current.temperature_2m,
        pressure_hpa=current.surface_pressure,
        humidity_percent=current.relative_humidity_2m,
        precipitation_mm=current.precipitation,
        conditions=_condition(current.weather_code),
        # The provider supplies no confidence score; missing must not be invented.
        confidence=None,
    )


def _average(values: list[Decimal]) -> Decimal | None:
    return None if not values else sum(values, Decimal(0)) / Decimal(len(values))


def _parse_historical(
    response: httpx.Response, *, started_at: datetime, ended_at: datetime
) -> HistoricalWeatherObservation:
    try:
        body = _HistoricalResponse.model_validate(response.json())
    except (ValidationError, ValueError) as exc:
        raise JobQueueError("weather_response_invalid") from exc
    hourly = body.hourly
    lengths = {
        len(hourly.time),
        len(hourly.temperature_2m),
        len(hourly.apparent_temperature),
        len(hourly.relative_humidity_2m),
        len(hourly.precipitation),
        len(hourly.weather_code),
        len(hourly.wind_speed_10m),
        len(hourly.wind_gusts_10m),
    }
    if len(lengths) != 1:
        raise JobQueueError("weather_response_invalid")
    expected_units = {
        "temperature_2m": "°C",
        "apparent_temperature": "°C",
        "relative_humidity_2m": "%",
        "precipitation": "mm",
        "wind_speed_10m": "km/h",
        "wind_gusts_10m": "km/h",
    }
    for field, expected in expected_units.items():
        if body.hourly_units.get(field) != expected:
            raise JobQueueError("weather_units_invalid")
    start_utc = started_at.astimezone(UTC)
    end_utc = ended_at.astimezone(UTC)
    floor_start = start_utc.replace(minute=0, second=0, microsecond=0)
    ceil_end = end_utc.replace(minute=0, second=0, microsecond=0)
    if ceil_end < end_utc:
        ceil_end += timedelta(hours=1)
    indexes: list[int] = []
    for index, timestamp in enumerate(hourly.time):
        try:
            observed = datetime.fromtimestamp(timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError) as exc:
            raise JobQueueError("weather_time_invalid") from exc
        if floor_start <= observed <= ceil_end:
            indexes.append(index)
    if not indexes:
        raise JobQueueError("weather_data_missing")

    def decimals(values: list[Decimal | None]) -> list[Decimal]:
        return [value for index in indexes if (value := values[index]) is not None]

    temperatures = decimals(hourly.temperature_2m)
    apparent = decimals(hourly.apparent_temperature)
    humidity = decimals(hourly.relative_humidity_2m)
    precipitation = decimals(hourly.precipitation)
    wind = decimals(hourly.wind_speed_10m)
    gusts = decimals(hourly.wind_gusts_10m)
    codes = [
        hourly.weather_code[index] for index in indexes if hourly.weather_code[index] is not None
    ]
    if not any((temperatures, apparent, humidity, precipitation, wind, gusts, codes)):
        raise JobQueueError("weather_data_missing")
    dominant_code = None
    if codes:
        counts = Counter(codes)
        dominant_code = min(counts, key=lambda code: (-counts[code], code))
    return HistoricalWeatherObservation(
        observed_at=start_utc,
        interval_ended_at=end_utc,
        timezone=body.timezone,
        temperature_c=_average(temperatures),
        apparent_temperature_c=(max(apparent) if apparent else None),
        humidity_percent=_average(humidity),
        precipitation_mm=(sum(precipitation, Decimal(0)) if precipitation else None),
        conditions=_condition(dominant_code),
        wind_speed_kph=_average(wind),
        wind_gust_kph=(max(gusts) if gusts else None),
        confidence=None,
    )


def fetch_current(
    latitude: Decimal,
    longitude: Decimal,
    *,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
) -> WeatherObservation:
    """Fetch current weather with bounded transient retries and safe errors."""
    if not 1 <= max_attempts <= 3:
        raise JobQueueError("weather_retry_policy_invalid")
    parameters = request_parameters(latitude, longitude)
    for attempt in range(1, max_attempts + 1):
        try:
            with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
                response = client.get(ENDPOINT, params=parameters)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt == max_attempts:
                raise JobQueueError("weather_provider_unavailable") from exc
            sleep(0.25 * (2 ** (attempt - 1)))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == max_attempts:
                raise JobQueueError("weather_provider_unavailable")
            sleep(0.25 * (2 ** (attempt - 1)))
            continue
        if response.is_error:
            raise JobQueueError("weather_request_rejected")
        return _parse(response)
    raise JobQueueError("weather_provider_unavailable")  # pragma: no cover


def fetch_historical_interval(
    latitude: Decimal,
    longitude: Decimal,
    *,
    started_at: datetime,
    ended_at: datetime,
    transport: httpx.BaseTransport | None = None,
    sleep: Callable[[float], None] = time.sleep,
    max_attempts: int = 3,
) -> HistoricalWeatherObservation:
    """Fetch hourly weather covering one activity interval with bounded retries."""
    if not 1 <= max_attempts <= 3:
        raise JobQueueError("weather_retry_policy_invalid")
    parameters = historical_request_parameters(
        latitude, longitude, started_at=started_at, ended_at=ended_at
    )
    for attempt in range(1, max_attempts + 1):
        try:
            with httpx.Client(timeout=TIMEOUT, transport=transport) as client:
                response = client.get(HISTORICAL_ENDPOINT, params=parameters)
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            if attempt == max_attempts:
                raise JobQueueError("weather_provider_unavailable") from exc
            sleep(0.25 * (2 ** (attempt - 1)))
            continue
        if response.status_code == 429 or response.status_code >= 500:
            if attempt == max_attempts:
                raise JobQueueError("weather_provider_unavailable")
            sleep(0.25 * (2 ** (attempt - 1)))
            continue
        if response.is_error:
            raise JobQueueError("weather_request_rejected")
        return _parse_historical(response, started_at=started_at, ended_at=ended_at)
    raise JobQueueError("weather_provider_unavailable")  # pragma: no cover
