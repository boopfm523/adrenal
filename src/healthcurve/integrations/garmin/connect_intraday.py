"""Bounded mapping for timestamped Garmin observations.

Raw private-API payloads are untrusted and ephemeral. This module selects only the
four series approved by ADR-0014 and deliberately treats null, sentinel, malformed,
and out-of-range values as missing rather than as zero-valued observations.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation
from typing import Any, Final
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from healthcurve.events.timekeeping import EventTime
from healthcurve.integrations.garmin.models import GarminMetricType

MAX_SAMPLES_PER_SERIES: Final = 10_000


@dataclass(frozen=True)
class IntradayObservation:
    event_time: EventTime
    metric_type: GarminMetricType
    value: Decimal
    unit: str
    field_name: str
    provider_id: str
    revision: str


@dataclass(frozen=True)
class MappedIntraday:
    observations: tuple[IntradayObservation, ...]
    warnings: tuple[str, ...]
    capabilities: dict[str, str]


def map_intraday_day(
    *,
    heart_rate: dict[str, Any],
    stress: dict[str, Any],
    respiration: dict[str, Any],
    hrv: dict[str, Any],
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
    observations.sort(key=lambda value: (value.event_time.occurred_at, value.metric_type.value))
    return MappedIntraday(
        observations=tuple(observations),
        warnings=tuple(sorted(set(warnings))),
        capabilities=capabilities,
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
        provider_id=provider_id,
        revision=_revision(selected),
    )


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
