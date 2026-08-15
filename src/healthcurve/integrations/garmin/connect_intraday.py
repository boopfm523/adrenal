"""Bounded mapping for timestamped Garmin observations.

Raw private-API payloads are untrusted and ephemeral. This module selects only the
approved series and deliberately treats null, sentinel, malformed,
and out-of-range values as missing rather than as zero-valued observations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, replace
from datetime import UTC, date, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from healthcurve.events.timekeeping import EventTime, resolve_event_time
from healthcurve.integrations.garmin.connect_mapping import DailyObservation
from healthcurve.integrations.garmin.models import GarminMetricType

MAX_SAMPLES_PER_SERIES: Final = 10_000


@dataclass(frozen=True)
class IntradayObservation:
    event_time: EventTime
    metric_type: GarminMetricType
    value: Decimal
    unit: str
    field_name: str
    sample_interval_seconds: int | None
    provider_id: str
    revision: str


@dataclass(frozen=True)
class MappedIntraday:
    observations: tuple[IntradayObservation, ...]
    aggregates: tuple[DailyObservation, ...]
    warnings: tuple[str, ...]
    capabilities: dict[str, str]


def map_intraday_day(
    *,
    day: date,
    heart_rate: dict[str, Any],
    stress: dict[str, Any],
    respiration: dict[str, Any],
    hrv: dict[str, Any],
    steps: list[Any],
    timezone: str,
) -> MappedIntraday:
    """Map one provider day without retaining the four raw response bodies."""

    zone = _zone(timezone)
    warnings: list[str] = []
    observations: list[IntradayObservation] = []
    capabilities: dict[str, str] = {}

    contracts = (
        (
            heart_rate,
            "heartRateValueDescriptors",
            "heartRateValues",
            "heartrate",
            GarminMetricType.HEART_RATE,
            "bpm",
            Decimal(1),
            Decimal(260),
        ),
        (
            stress,
            "stressValueDescriptorsDTOList",
            "stressValuesArray",
            "stressLevel",
            GarminMetricType.STRESS,
            "garmin_score",
            Decimal(0),
            Decimal(100),
        ),
        (
            respiration,
            "respirationValueDescriptorsDTOList",
            "respirationValuesArray",
            "respiration",
            GarminMetricType.RESPIRATION_RATE,
            "breaths/min",
            Decimal("0.1"),
            Decimal(100),
        ),
    )
    for payload, descriptors_key, values_key, value_key, metric_type, unit, low, high in contracts:
        mapped = _map_descriptor_pairs(
            payload=payload,
            descriptors_key=descriptors_key,
            values_key=values_key,
            value_key=value_key,
            metric_type=metric_type,
            unit=unit,
            minimum=low,
            maximum=high,
            zone=zone,
            warnings=warnings,
        )
        observations.extend(mapped)
        capabilities[f"intraday_{metric_type.value}"] = "available" if mapped else "unavailable"

    mapped_hrv = _map_hrv(hrv, zone=zone, warnings=warnings)
    observations.extend(mapped_hrv)
    capabilities["intraday_hrv"] = "available" if mapped_hrv else "unavailable"
    mapped_steps = _map_hourly_steps(day=day, payload=steps, zone=zone, warnings=warnings)
    observations.extend(mapped_steps)
    capabilities["intraday_steps"] = "available" if mapped_steps else "unavailable"
    aggregates = _map_aggregates(
        day=day,
        respiration=respiration,
        hrv=hrv,
        timezone=zone.key,
        warnings=warnings,
        capabilities=capabilities,
    )
    observations = _with_observed_intervals(observations)
    observations.sort(key=lambda value: (value.event_time.occurred_at, value.metric_type.value))
    return MappedIntraday(
        observations=tuple(observations),
        aggregates=tuple(aggregates),
        warnings=tuple(sorted(set(warnings))),
        capabilities=capabilities,
    )


def _map_aggregates(
    *,
    day: date,
    respiration: dict[str, Any],
    hrv: dict[str, Any],
    timezone: str,
    warnings: list[str],
    capabilities: dict[str, str],
) -> list[DailyObservation]:
    """Select provider-defined aggregates without inventing an intraday instant."""

    output: list[DailyObservation] = []
    capabilities["hrv_daily_average"] = "unsupported"
    hrv_summary = hrv.get("hrvSummary")
    if hrv_summary is not None and not isinstance(hrv_summary, dict):
        warnings.append("hrv_nightly_average_shape_invalid")
        hrv_summary = {}
    hrv_summary = hrv_summary if isinstance(hrv_summary, dict) else {}
    _append_aggregate(
        output,
        day=day,
        payload=hrv_summary,
        field_name="lastNightAvg",
        capability="hrv_nightly_average",
        metric_type=GarminMetricType.HRV,
        unit="ms",
        minimum=Decimal("0.1"),
        maximum=Decimal(1_000),
        timezone=timezone,
        warnings=warnings,
        capabilities=capabilities,
    )
    for field_name, capability in (
        ("avgWakingRespirationValue", "respiration_waking_average"),
        ("avgSleepRespirationValue", "respiration_sleep_average"),
        ("lowestRespirationValue", "respiration_daily_low"),
        ("highestRespirationValue", "respiration_daily_high"),
    ):
        _append_aggregate(
            output,
            day=day,
            payload=respiration,
            field_name=field_name,
            capability=capability,
            metric_type=GarminMetricType.RESPIRATION_RATE,
            unit="breaths/min",
            minimum=Decimal("0.1"),
            maximum=Decimal(100),
            timezone=timezone,
            warnings=warnings,
            capabilities=capabilities,
        )
    return output


def _append_aggregate(
    output: list[DailyObservation],
    *,
    day: date,
    payload: dict[str, Any],
    field_name: str,
    capability: str,
    metric_type: GarminMetricType,
    unit: str,
    minimum: Decimal,
    maximum: Decimal,
    timezone: str,
    warnings: list[str],
    capabilities: dict[str, str],
) -> None:
    value = _bounded_decimal(payload.get(field_name), minimum, maximum)
    if value is None:
        capabilities[capability] = "unavailable"
        if field_name in payload and payload.get(field_name) is not None:
            warnings.append(f"{capability}_invalid")
        return
    capabilities[capability] = "available"
    selected = {
        "day": day.isoformat(),
        "field": field_name,
        "metric_type": metric_type.value,
        "unit": unit,
        "value": str(value),
    }
    output.append(
        DailyObservation(
            day=day,
            event_time=resolve_event_time(datetime.combine(day, datetime.min.time()), timezone),
            metric_type=metric_type,
            value=value,
            unit=unit,
            field_name=field_name,
            provider_id=f"aggregate:{day.isoformat()}:{metric_type.value}:{field_name}",
            revision=_revision(selected),
        )
    )


def _map_descriptor_pairs(
    *,
    payload: dict[str, Any],
    descriptors_key: str,
    values_key: str,
    value_key: str,
    metric_type: GarminMetricType,
    unit: str,
    minimum: Decimal,
    maximum: Decimal,
    zone: ZoneInfo,
    warnings: list[str],
) -> list[IntradayObservation]:
    indexes = _descriptor_indexes(payload.get(descriptors_key))
    if indexes.get("timestamp") is None or indexes.get(value_key) is None:
        if payload:
            warnings.append(f"intraday_{metric_type.value}_shape_invalid")
        return []
    rows = payload.get(values_key)
    if not isinstance(rows, list):
        if payload:
            warnings.append(f"intraday_{metric_type.value}_shape_invalid")
        return []
    if len(rows) > MAX_SAMPLES_PER_SERIES:
        warnings.append(f"intraday_{metric_type.value}_truncated")
    output: list[IntradayObservation] = []
    seen: set[datetime] = set()
    missing = False
    timestamp_index = indexes["timestamp"]
    value_index = indexes[value_key]
    for row in rows[:MAX_SAMPLES_PER_SERIES]:
        if not isinstance(row, list) or max(timestamp_index, value_index) >= len(row):
            missing = True
            continue
        occurred_at = _epoch_millis(row[timestamp_index])
        value = _bounded_decimal(row[value_index], minimum, maximum)
        if occurred_at is None or value is None:
            missing = True
            continue
        if occurred_at in seen:
            warnings.append(f"intraday_{metric_type.value}_duplicate_timestamp")
            continue
        seen.add(occurred_at)
        output.append(_observation(occurred_at, metric_type, value, unit, value_key, zone))
    if missing:
        warnings.append(f"intraday_{metric_type.value}_missing_or_invalid")
    return output


def _map_hrv(
    payload: dict[str, Any], *, zone: ZoneInfo, warnings: list[str]
) -> list[IntradayObservation]:
    rows = payload.get("hrvReadings")
    if not isinstance(rows, list):
        if payload:
            warnings.append("intraday_hrv_shape_invalid")
        return []
    if len(rows) > MAX_SAMPLES_PER_SERIES:
        warnings.append("intraday_hrv_truncated")
    output: list[IntradayObservation] = []
    seen: set[datetime] = set()
    missing = False
    for row in rows[:MAX_SAMPLES_PER_SERIES]:
        if not isinstance(row, dict):
            missing = True
            continue
        occurred_at = _parse_gmt(row.get("readingTimeGMT"))
        value = _bounded_decimal(row.get("hrvValue"), Decimal("0.1"), Decimal(1_000))
        if occurred_at is None or value is None:
            missing = True
            continue
        if occurred_at in seen:
            warnings.append("intraday_hrv_duplicate_timestamp")
            continue
        seen.add(occurred_at)
        output.append(
            _observation(occurred_at, GarminMetricType.HRV, value, "ms", "hrvValue", zone)
        )
    if missing:
        warnings.append("intraday_hrv_missing_or_invalid")
    return output


def _map_hourly_steps(
    *, day: date, payload: list[Any], zone: ZoneInfo, warnings: list[str]
) -> list[IntradayObservation]:
    """Sum provider step buckets into observed local-clock hours.

    Garmin currently returns bounded sub-hour intervals with explicit GMT bounds.
    Only valid intervals whose local start belongs to the requested provider day are
    included. Missing buckets stay missing; an observed zero remains a real zero.
    """

    if len(payload) > MAX_SAMPLES_PER_SERIES:
        warnings.append("intraday_steps_truncated")
    hourly: dict[datetime, Decimal] = {}
    missing = False
    for row in payload[:MAX_SAMPLES_PER_SERIES]:
        if not isinstance(row, dict):
            missing = True
            continue
        started_at = _parse_gmt(row.get("startGMT"))
        ended_at = _parse_gmt(row.get("endGMT"))
        value = _bounded_decimal(row.get("steps"), Decimal(0), Decimal(1_000_000))
        if started_at is None or ended_at is None or ended_at <= started_at or value is None:
            missing = True
            continue
        local = started_at.astimezone(zone)
        if local.date() != day:
            missing = True
            continue
        local_hour = local.replace(minute=0, second=0, microsecond=0)
        hour = local_hour.astimezone(UTC)
        hourly[hour] = hourly.get(hour, Decimal(0)) + value
    if missing:
        warnings.append("intraday_steps_missing_or_invalid")
    return [
        replace(
            _observation(
                occurred_at,
                GarminMetricType.STEPS,
                value,
                "steps",
                "hourlySteps",
                zone,
            ),
            sample_interval_seconds=3_600,
        )
        for occurred_at, value in sorted(hourly.items())
    ]


def _descriptor_indexes(value: Any) -> dict[str, int]:
    if not isinstance(value, list) or len(value) > 32:
        return {}
    output: dict[str, int] = {}
    for descriptor in value:
        if not isinstance(descriptor, dict):
            continue
        key = descriptor.get("key")
        index = descriptor.get("index")
        if isinstance(key, str) and isinstance(index, int) and 0 <= index < 32:
            output[key] = index
    return output


def _observation(
    occurred_at: datetime,
    metric_type: GarminMetricType,
    value: Decimal,
    unit: str,
    field_name: str,
    zone: ZoneInfo,
) -> IntradayObservation:
    local = occurred_at.astimezone(zone)
    offset = local.utcoffset()
    if offset is None:  # pragma: no cover - ZoneInfo always resolves an offset
        raise ValueError("garmin_timezone_invalid")
    event_time = EventTime(
        occurred_at=occurred_at,
        local_time=local.replace(tzinfo=None),
        timezone=zone.key,
        utc_offset_minutes=int(offset.total_seconds() / 60),
    )
    selected = {
        "metric_type": metric_type.value,
        "occurred_at": occurred_at.isoformat(),
        "value": str(value),
        "unit": unit,
    }
    provider_id = f"intraday:{metric_type.value}:{occurred_at.isoformat()}"
    return IntradayObservation(
        event_time=event_time,
        metric_type=metric_type,
        value=value,
        unit=unit,
        field_name=field_name,
        sample_interval_seconds=None,
        provider_id=provider_id,
        revision=_revision(selected),
    )


def _with_observed_intervals(
    observations: list[IntradayObservation],
) -> list[IntradayObservation]:
    """Attach elapsed cadence since the prior valid sample of the same series."""

    prior: dict[GarminMetricType, datetime] = {}
    output: list[IntradayObservation] = []
    for observation in sorted(
        observations, key=lambda value: (value.metric_type.value, value.event_time.occurred_at)
    ):
        previous = prior.get(observation.metric_type)
        interval = observation.sample_interval_seconds
        if previous is not None:
            elapsed = int((observation.event_time.occurred_at - previous).total_seconds())
            interval = elapsed if elapsed > 0 else None
        prior[observation.metric_type] = observation.event_time.occurred_at
        output.append(
            replace(
                observation,
                sample_interval_seconds=interval,
                revision=_revision(
                    {
                        "observation_revision": observation.revision,
                        "sample_interval_seconds": interval,
                    }
                ),
            )
        )
    return output


def _epoch_millis(value: Any) -> datetime | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    try:
        return datetime.fromtimestamp(float(value) / 1_000, UTC)
    except (OverflowError, OSError, ValueError):
        return None


def _parse_gmt(value: Any) -> datetime | None:
    if not isinstance(value, str) or len(value) > 64:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=parsed.tzinfo or UTC).astimezone(UTC)


def _bounded_decimal(value: Any, minimum: Decimal, maximum: Decimal) -> Decimal | None:
    if isinstance(value, bool) or value is None:
        return None
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return None
    return parsed if parsed.is_finite() and minimum <= parsed <= maximum else None


def _zone(value: str) -> ZoneInfo:
    try:
        return ZoneInfo(value)
    except ZoneInfoNotFoundError as exc:
        raise ValueError("garmin_timezone_invalid") from exc


def _revision(value: dict[str, Any]) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(body.encode()).hexdigest()
