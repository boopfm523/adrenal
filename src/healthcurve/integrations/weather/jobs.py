"""Durable weather enrichment jobs with opaque, non-health payloads."""

from __future__ import annotations

import hashlib
import uuid
from collections.abc import Callable, Mapping
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.context.models import (
    ContextEvent,
    LocationPrecision,
    PrecipitationUnit,
    PressureUnit,
    TemperatureUnit,
)
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.timekeeping import UnknownTimezoneError, from_instant
from healthcurve.integrations.garmin.models import GarminActivityEvent
from healthcurve.integrations.weather import open_meteo
from healthcurve.operations.jobs import Job, JobQueueError, enqueue
from healthcurve.operations.worker import JobHandler

WEATHER_ENRICHMENT_TASK = "context.weather.enrich"
GARMIN_ACTIVITY_WEATHER_TASK = "garmin.activity.weather.enrich"


def enqueue_weather_enrichment(session: Session, context: ContextEvent) -> Job:
    """Queue only an opaque fact ID; coordinates and health text never enter ops.job."""
    return enqueue(
        session,
        task=WEATHER_ENRICHMENT_TASK,
        payload={"context_event_id": str(context.id)},
        idempotency_key=f"context:{context.id}",
        priority=25,
        max_attempts=3,
    )


def enqueue_activity_weather_enrichment(
    session: Session, activity: GarminActivityEvent
) -> Job | None:
    """Queue an eligible activity by opaque ID; no coordinates enter the queue."""
    if (
        activity.environment != "outdoor"
        or activity.sport not in {"walking", "running"}
        or activity.location_latitude is None
        or activity.location_longitude is None
    ):
        return None
    return enqueue(
        session,
        task=GARMIN_ACTIVITY_WEATHER_TASK,
        payload={"activity_event_id": str(activity.id)},
        idempotency_key=f"garmin-activity-weather:{activity.id}",
        priority=25,
        max_attempts=3,
    )


def _source_event(session: Session, payload: Mapping[str, object]) -> ContextEvent:
    if set(payload) != {"context_event_id"}:
        raise JobQueueError("weather_job_payload_invalid")
    try:
        event_id = uuid.UUID(str(payload["context_event_id"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise JobQueueError("weather_job_payload_invalid") from exc
    source = session.get(ContextEvent, event_id)
    if source is None:
        raise JobQueueError("weather_source_missing")
    if source.latitude is None or source.longitude is None:
        raise JobQueueError("weather_source_location_missing")
    if source.location_precision is not LocationPrecision.COARSE or source.exact_location_consent:
        raise JobQueueError("weather_source_precision_invalid")
    return source


def _revision(observation: open_meteo.WeatherObservation) -> str:
    values = "|".join(
        str(value)
        for value in (
            observation.observed_at.isoformat(),
            observation.temperature_c,
            observation.pressure_hpa,
            observation.humidity_percent,
            observation.precipitation_mm,
            observation.conditions,
        )
    )
    return hashlib.sha256(values.encode()).hexdigest()


def make_weather_handler(
    *,
    fetch: Callable[[Decimal, Decimal], open_meteo.WeatherObservation] = (open_meteo.fetch_current),
) -> JobHandler:
    """Build an injectable job handler; provider failures remain privacy-safe codes."""

    def handle(session: Session, payload: Mapping[str, object]) -> None:
        source = _source_event(session, payload)
        assert source.latitude is not None and source.longitude is not None
        observation = fetch(source.latitude, source.longitude)
        provider_id = f"{open_meteo.PROVIDER}:{source.id}"
        revision = _revision(observation)
        existing = session.scalar(
            select(ContextEvent.id).where(
                ContextEvent.source_type == SourceType.PROVIDER,
                ContextEvent.provider_id == provider_id,
                ContextEvent.source_revision == revision,
            )
        )
        if existing is not None:
            return
        try:
            event_time = from_instant(observation.observed_at, observation.timezone)
        except (UnknownTimezoneError, ValueError) as exc:
            raise JobQueueError("weather_timezone_invalid") from exc
        observation_id = hashlib.sha256(
            f"{provider_id}:{observation.observed_at.isoformat()}".encode()
        ).hexdigest()
        events.create_event(
            session,
            ContextEvent,
            owner_id=source.owner_id,
            event_time=event_time,
            source_type=SourceType.PROVIDER,
            confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
            location_precision=source.location_precision,
            coarse_location_label=source.coarse_location_label,
            latitude=source.latitude,
            longitude=source.longitude,
            exact_location_consent=False,
            weather_provider=open_meteo.PROVIDER,
            weather_observation_id=observation_id,
            weather_observed_at=observation.observed_at,
            temperature=observation.temperature_c,
            temperature_unit=(
                TemperatureUnit.CELSIUS if observation.temperature_c is not None else None
            ),
            pressure=observation.pressure_hpa,
            pressure_unit=(PressureUnit.HPA if observation.pressure_hpa is not None else None),
            humidity_percent=observation.humidity_percent,
            precipitation=observation.precipitation_mm,
            precipitation_unit=(
                PrecipitationUnit.MM if observation.precipitation_mm is not None else None
            ),
            conditions=observation.conditions,
            weather_confidence=observation.confidence,
            provider_id=provider_id,
            source_revision=revision,
        )

    return handle


def _activity_event(session: Session, payload: Mapping[str, object]) -> GarminActivityEvent | None:
    if set(payload) != {"activity_event_id"}:
        raise JobQueueError("activity_weather_job_payload_invalid")
    try:
        event_id = uuid.UUID(str(payload["activity_event_id"]))
    except (ValueError, TypeError, AttributeError) as exc:
        raise JobQueueError("activity_weather_job_payload_invalid") from exc
    activity = session.get(GarminActivityEvent, event_id)
    if activity is None:
        return None
    superseded = session.scalar(
        select(GarminActivityEvent.id).where(GarminActivityEvent.supersedes_id == activity.id)
    )
    return None if superseded is not None else activity


def _activity_revision(observation: open_meteo.HistoricalWeatherObservation) -> str:
    values = "|".join(
        str(value)
        for value in (
            observation.observed_at.isoformat(),
            observation.interval_ended_at.isoformat(),
            observation.temperature_c,
            observation.apparent_temperature_c,
            observation.humidity_percent,
            observation.precipitation_mm,
            observation.conditions,
            observation.wind_speed_kph,
            observation.wind_gust_kph,
        )
    )
    return hashlib.sha256(values.encode()).hexdigest()


def make_activity_weather_handler(
    *,
    fetch: Callable[..., open_meteo.HistoricalWeatherObservation] = (
        open_meteo.fetch_historical_interval
    ),
) -> JobHandler:
    """Build the private historical-weather handler for supported outdoor activity."""

    def handle(session: Session, payload: Mapping[str, object]) -> None:
        activity = _activity_event(session, payload)
        if activity is None:
            return
        if activity.environment != "outdoor" or activity.sport not in {"walking", "running"}:
            return
        if activity.location_latitude is None or activity.location_longitude is None:
            return
        observation = fetch(
            activity.location_latitude,
            activity.location_longitude,
            started_at=activity.occurred_at,
            ended_at=activity.ended_at,
        )
        provider_id = f"{open_meteo.PROVIDER}:garmin-activity:{activity.id}"
        revision = _activity_revision(observation)
        existing = session.scalar(
            select(ContextEvent.id).where(
                ContextEvent.owner_id == activity.owner_id,
                ContextEvent.source_type == SourceType.PROVIDER,
                ContextEvent.provider_id == provider_id,
                ContextEvent.source_revision == revision,
            )
        )
        if existing is not None:
            return
        try:
            event_time = from_instant(observation.observed_at, activity.timezone)
        except (UnknownTimezoneError, ValueError) as exc:
            raise JobQueueError("weather_timezone_invalid") from exc
        observation_id = hashlib.sha256(f"{provider_id}:{revision}".encode()).hexdigest()
        events.create_event(
            session,
            ContextEvent,
            owner_id=activity.owner_id,
            event_time=event_time,
            source_type=SourceType.PROVIDER,
            confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
            location_precision=LocationPrecision.COARSE,
            coarse_location_label=activity.location_name or "Garmin activity area",
            latitude=activity.location_latitude,
            longitude=activity.location_longitude,
            exact_location_consent=False,
            weather_provider=open_meteo.PROVIDER,
            weather_observation_id=observation_id,
            weather_observed_at=observation.observed_at,
            weather_interval_ended_at=observation.interval_ended_at,
            temperature=observation.temperature_c,
            apparent_temperature=(
                observation.apparent_temperature_c
                if observation.temperature_c is not None
                else None
            ),
            temperature_unit=(
                TemperatureUnit.CELSIUS if observation.temperature_c is not None else None
            ),
            pressure=None,
            pressure_unit=None,
            humidity_percent=observation.humidity_percent,
            precipitation=observation.precipitation_mm,
            precipitation_unit=(
                PrecipitationUnit.MM if observation.precipitation_mm is not None else None
            ),
            conditions=observation.conditions,
            wind_speed_kph=observation.wind_speed_kph,
            wind_gust_kph=observation.wind_gust_kph,
            weather_confidence=observation.confidence,
            provider_id=provider_id,
            source_revision=revision,
        )

    return handle
