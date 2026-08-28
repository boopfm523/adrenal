"""Deterministic mapping of untrusted Garmin Connect responses into scoped facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from healthcurve.events.timekeeping import EventTime, resolve_event_time
from healthcurve.integrations.garmin.models import GarminMetricType

METERS_PER_MILE: Final = Decimal("1609.344")
MAX_ACTIVITY_SECONDS: Final = Decimal(31 * 24 * 60 * 60)


@dataclass(frozen=True)
class DailyObservation:
    day: date
    event_time: EventTime
    metric_type: GarminMetricType
    value: Decimal
    unit: str
    field_name: str
    provider_id: str
    revision: str


@dataclass(frozen=True)
class SleepStageObservation:
    stage: str
    started_at: datetime
    ended_at: datetime


@dataclass(frozen=True)
class SleepObservation:
    event_time: EventTime
    ended_at: datetime
    duration_seconds: int | None
    duration_source: str
    awakenings: int | None
    score: int | None
    stage_count: int
    stage_intervals: tuple[SleepStageObservation, ...]
    provider_id: str
    revision: str


@dataclass(frozen=True)
class ActivityObservation:
    event_time: EventTime
    ended_at: datetime
    sport: str
    title: str | None
    elapsed_seconds: Decimal | None
    distance_miles: Decimal | None
    environment: str
    location_name: str | None
    latitude: Decimal | None
    longitude: Decimal | None
    provider_id: str
    revision: str


@dataclass(frozen=True)
class MappedDay:
    metrics: tuple[DailyObservation, ...]
    sleep: SleepObservation | None
    warnings: tuple[str, ...]
    capabilities: dict[str, str]


def map_day(*, day: date, stats: dict[str, Any], sleep: dict[str, Any], timezone: str) -> MappedDay:
    zone = _zone(timezone)
    event_time = resolve_event_time(datetime.combine(day, time.min), zone.key)
    warnings: list[str] = []
    metrics: list[DailyObservation] = []
    capabilities: dict[str, str] = {}
    for field, metric_type, unit, upper in (
        ("totalSteps", GarminMetricType.STEPS, "steps", 1_000_000),
        ("restingHeartRate", GarminMetricType.RESTING_HEART_RATE, "bpm", 260),
        ("averageStressLevel", GarminMetricType.STRESS, "garmin_score", 100),
    ):
        value = _bounded_decimal(stats.get(field), Decimal(0), Decimal(upper))
        capability = metric_type.value
        if value is None:
            capabilities[capability] = "unavailable"
            if field in stats and stats.get(field) is not None:
                warnings.append(f"{capability}_invalid")
            continue
        capabilities[capability] = "available"
        selected = {"day": day.isoformat(), "field": field, "value": str(value)}
        metrics.append(
            DailyObservation(
                day=day,
                event_time=event_time,
                metric_type=metric_type,
                value=value,
                unit=unit,
                field_name=field,
                provider_id=f"daily:{day.isoformat()}:{metric_type.value}",
                revision=_revision(selected),
            )
        )

    sleep_observation = _map_sleep(day=day, payload=sleep, timezone=zone.key, warnings=warnings)
    capabilities["sleep"] = "available" if sleep_observation is not None else "unavailable"
    return MappedDay(tuple(metrics), sleep_observation, tuple(sorted(set(warnings))), capabilities)


def map_activities(
    payload: list[dict[str, Any]], *, timezone: str
) -> tuple[tuple[ActivityObservation, ...], tuple[str, ...]]:
    _zone(timezone)
    observations: list[ActivityObservation] = []
    warnings: list[str] = []
    for raw in payload[:10_000]:
        provider_value = raw.get("activityId")
        start_gmt = _parse_iso(raw.get("startTimeGMT"), utc=True)
        start_local = _parse_iso(raw.get("startTimeLocal"), utc=False)
        activity_type = raw.get("activityType")
        sport = activity_type.get("typeKey") if isinstance(activity_type, dict) else None
        if provider_value is None or start_gmt is None or start_local is None or not sport:
            warnings.append("activity_required_field_missing")
            continue
        elapsed = _bounded_decimal(
            raw.get("elapsedDuration", raw.get("duration")), Decimal(0), MAX_ACTIVITY_SECONDS
        )
        if elapsed is None or elapsed <= 0:
            warnings.append("activity_duration_invalid")
            continue
        distance_m = _bounded_decimal(raw.get("distance"), Decimal(0), Decimal("100000000"))
        distance_miles = None if distance_m is None else distance_m / METERS_PER_MILE
        ended_at = start_gmt + timedelta(seconds=float(elapsed))
        event_time = _provider_event_time(start_gmt, start_local, raw, timezone, warnings)
        normalized_sport = _slug(str(sport))
        environment = _activity_environment(normalized_sport)
        latitude, longitude = _coarse_activity_coordinates(raw, warnings)
        location_name = _text(raw.get("locationName"), 120)
        selected = {
            "activityId": str(provider_value),
            "startTimeGMT": start_gmt.isoformat(),
            "startTimeLocal": start_local.isoformat(),
            "sport": normalized_sport,
            "title": _text(raw.get("activityName"), 300),
            "elapsed": str(elapsed),
            "distance_miles": None if distance_miles is None else str(distance_miles),
            "environment": environment,
            "location_name": location_name,
            "latitude": None if latitude is None else str(latitude),
            "longitude": None if longitude is None else str(longitude),
        }
        observations.append(
            ActivityObservation(
                event_time=event_time,
                ended_at=ended_at,
                sport=normalized_sport,
                title=_text(raw.get("activityName"), 300),
                elapsed_seconds=elapsed,
                distance_miles=distance_miles,
                environment=environment,
                location_name=location_name,
                latitude=latitude,
                longitude=longitude,
                provider_id=f"activity:{provider_value}",
                revision=_revision(selected),
            )
        )
    if len(payload) > 10_000:
        warnings.append("activity_response_truncated")
    return tuple(observations), tuple(sorted(set(warnings)))


def _map_sleep(
    *, day: date, payload: dict[str, Any], timezone: str, warnings: list[str]
) -> SleepObservation | None:
    dto = payload.get("dailySleepDTO")
    if not isinstance(dto, dict):
        return None
    start_gmt = _epoch_millis(dto.get("sleepStartTimestampGMT"), utc=True)
    end_gmt = _epoch_millis(dto.get("sleepEndTimestampGMT"), utc=True)
    start_local = _epoch_millis(dto.get("sleepStartTimestampLocal"), utc=False)
    if start_gmt is None or end_gmt is None or start_local is None or end_gmt <= start_gmt:
        if any(key in dto for key in ("sleepStartTimestampGMT", "sleepEndTimestampGMT")):
            warnings.append("sleep_bounds_invalid")
        return None
    duration = _bounded_int(dto.get("sleepTimeSeconds"), 0, 172_800)
    duration_source = "provider"
    if duration is None:
        duration = int((end_gmt - start_gmt).total_seconds())
        duration_source = "calculated_from_bounds"
    awakenings = _first_bounded_int(
        dto, ("awakeCount", "numberOfAwakenings", "awakeCountValue"), 0, 1_000
    )
    scores = dto.get("sleepScores")
    overall = scores.get("overall") if isinstance(scores, dict) else None
    score = _bounded_int(overall.get("value"), 0, 100) if isinstance(overall, dict) else None
    stage_intervals, stage_count = _map_sleep_stages(
        payload=payload,
        dto=dto,
        sleep_start=start_gmt,
        sleep_end=end_gmt,
        warnings=warnings,
    )
    event_time = _provider_event_time(start_gmt, start_local, dto, timezone, warnings)
    selected = {
        "day": day.isoformat(),
        "start": start_gmt.isoformat(),
        "end": end_gmt.isoformat(),
        "duration": duration,
        "duration_source": duration_source,
        "awakenings": awakenings,
        "score": score,
        "stage_count": stage_count,
        "stage_intervals": [
            {
                "stage": interval.stage,
                "started_at": interval.started_at.isoformat(),
                "ended_at": interval.ended_at.isoformat(),
            }
            for interval in stage_intervals
        ],
    }
    return SleepObservation(
        event_time=event_time,
        ended_at=end_gmt,
        duration_seconds=duration,
        duration_source=duration_source,
        awakenings=awakenings,
        score=score,
        stage_count=stage_count,
        stage_intervals=stage_intervals,
        provider_id=f"sleep:{day.isoformat()}",
        revision=_revision(selected),
    )


def _map_sleep_stages(
    *,
    payload: dict[str, Any],
    dto: dict[str, Any],
    sleep_start: datetime,
    sleep_end: datetime,
    warnings: list[str],
) -> tuple[tuple[SleepStageObservation, ...], int]:
    raw_levels = payload.get("sleepLevels")
    if not isinstance(raw_levels, list):
        raw_levels = dto.get("sleepLevels")
    if not isinstance(raw_levels, list):
        return (), 0
    if len(raw_levels) > 10_000:
        warnings.append("sleep_stages_truncated")
    valid_count = 0
    awake: list[SleepStageObservation] = []
    for raw in raw_levels[:10_000]:
        if not isinstance(raw, dict):
            warnings.append("sleep_stage_invalid")
            continue
        started_at = _first_stage_time(raw, ("startGMT", "startTimeGMT", "startTimestampGMT"))
        ended_at = _first_stage_time(raw, ("endGMT", "endTimeGMT", "endTimestampGMT"))
        if (
            started_at is None
            or ended_at is None
            or ended_at <= started_at
            or started_at < sleep_start
            or ended_at > sleep_end
        ):
            warnings.append("sleep_stage_bounds_invalid")
            continue
        valid_count += 1
        if not _is_awake_stage(raw.get("activityLevel", raw.get("sleepLevel", raw.get("stage")))):
            continue
        if awake and started_at < awake[-1].ended_at:
            warnings.append("sleep_awake_interval_overlap")
            continue
        awake.append(SleepStageObservation("awake", started_at, ended_at))
    return tuple(awake), valid_count


def _first_stage_time(payload: dict[str, Any], keys: tuple[str, ...]) -> datetime | None:
    for key in keys:
        value = payload.get(key)
        parsed = (
            _epoch_millis(value, utc=True)
            if isinstance(value, int | float)
            else _parse_iso(value, utc=True)
        )
        if parsed is not None:
            return parsed
    return None


def _is_awake_stage(value: Any) -> bool:
    if isinstance(value, bool):
        return False
    if isinstance(value, int | float):
        return int(value) == 3
    return isinstance(value, str) and value.strip().casefold().replace("_", " ") in {
        "awake",
        "wake",
        "waking",
    }


def _provider_event_time(
    occurred_at: datetime,
    local_time: datetime,
    payload: dict[str, Any],
    fallback_timezone: str,
    warnings: list[str],
) -> EventTime:
    zone_name = _payload_timezone(payload) or fallback_timezone
    zone = _zone(zone_name)
    expected = occurred_at.astimezone(zone).replace(tzinfo=None)
    if expected != local_time:
        warnings.append("timezone_fallback_mismatch")
        local_time = expected
    zone_offset = occurred_at.astimezone(zone).utcoffset()
    if zone_offset is None:  # pragma: no cover - ZoneInfo always has an offset
        raise ValueError("garmin_timezone_invalid")
    offset = int(zone_offset.total_seconds() / 60)
    return EventTime(
        occurred_at=occurred_at.astimezone(UTC),
        local_time=local_time,
        timezone=zone.key,
        utc_offset_minutes=offset,
    )


def _payload_timezone(payload: dict[str, Any]) -> str | None:
    for key in ("timeZoneId", "timezone", "timeZone"):
        value = payload.get(key)
        if isinstance(value, str):
            try:
                return ZoneInfo(value).key
            except ZoneInfoNotFoundError:
                continue
    return None


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("garmin_timezone_invalid") from exc


def _parse_iso(value: Any, *, utc: bool) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if utc:
        return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)
    return parsed.replace(tzinfo=None)


def _epoch_millis(value: Any, *, utc: bool) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        parsed = datetime.fromtimestamp(float(value) / 1000, UTC)
    except (OverflowError, OSError, ValueError):
        return None
    return parsed if utc else parsed.replace(tzinfo=None)


def _bounded_decimal(value: Any, minimum: Decimal, maximum: Decimal) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and minimum <= parsed <= maximum else None


def _activity_environment(sport: str) -> str:
    if sport in {
        "indoor_walking",
        "treadmill_walking",
        "indoor_running",
        "treadmill_running",
        "treadmill",
        "rowing",
        "indoor_rowing",
        "rowing_machine",
        "indoor_rowing_machine",
    }:
        return "indoor"
    if sport in {"walking", "running"}:
        return "outdoor"
    return "unknown"


def _coarse_activity_coordinates(
    payload: dict[str, Any], warnings: list[str]
) -> tuple[Decimal | None, Decimal | None]:
    raw_latitude = payload.get("startLatitude")
    raw_longitude = payload.get("startLongitude")
    if raw_latitude is None and raw_longitude is None:
        return None, None
    latitude = _bounded_decimal(raw_latitude, Decimal("-90"), Decimal("90"))
    longitude = _bounded_decimal(raw_longitude, Decimal("-180"), Decimal("180"))
    if latitude is None or longitude is None:
        warnings.append("activity_location_invalid")
        return None, None
    precision = Decimal("0.1")
    return (
        latitude.quantize(precision, rounding=ROUND_HALF_UP),
        longitude.quantize(precision, rounding=ROUND_HALF_UP),
    )


def _bounded_int(value: Any, minimum: int, maximum: int) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _first_bounded_int(
    payload: dict[str, Any], keys: tuple[str, ...], minimum: int, maximum: int
) -> int | None:
    for key in keys:
        if key in payload:
            value = _bounded_int(payload[key], minimum, maximum)
            if value is not None:
                return value
    return None


def _text(value: Any, maximum: int) -> str | None:
    if not isinstance(value, str):
        return None
    cleaned = " ".join(value.split()).strip()
    return cleaned[:maximum] or None


def _slug(value: str) -> str:
    cleaned = "_".join(value.casefold().split())
    normalized = "".join(
        character for character in cleaned if character.isalnum() or character == "_"
    )
    normalized = "_".join(part for part in normalized.split("_") if part)[:80]
    return normalized or "other"


def _revision(value: dict[str, Any]) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()
