"""Deterministic mapping of untrusted Garmin Connect responses into scoped facts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal, InvalidOperation
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
class SleepObservation:
    event_time: EventTime
    ended_at: datetime
    duration_seconds: int | None
    duration_source: str
    awakenings: int | None
    score: int | None
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
        selected = {
            "activityId": str(provider_value),
            "startTimeGMT": start_gmt.isoformat(),
            "startTimeLocal": start_local.isoformat(),
            "sport": str(sport),
            "title": _text(raw.get("activityName"), 300),
            "elapsed": str(elapsed),
            "distance_miles": None if distance_miles is None else str(distance_miles),
        }
        observations.append(
            ActivityObservation(
                event_time=event_time,
                ended_at=ended_at,
                sport=_slug(str(sport)),
                title=_text(raw.get("activityName"), 300),
                elapsed_seconds=elapsed,
                distance_miles=distance_miles,
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
    event_time = _provider_event_time(start_gmt, start_local, dto, timezone, warnings)
    selected = {
        "day": day.isoformat(),
        "start": start_gmt.isoformat(),
        "end": end_gmt.isoformat(),
        "duration": duration,
        "duration_source": duration_source,
        "awakenings": awakenings,
        "score": score,
    }
    return SleepObservation(
        event_time=event_time,
        ended_at=end_gmt,
        duration_seconds=duration,
        duration_source=duration_source,
        awakenings=awakenings,
        score=score,
        provider_id=f"sleep:{day.isoformat()}",
        revision=_revision(selected),
    )


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
