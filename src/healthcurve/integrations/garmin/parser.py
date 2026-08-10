"""Bounded local parsing of owner-exported Garmin FIT, CSV, and ZIP files.

The adapter maps only fields named by Garmin's official FIT profile or recognized
Garmin Connect activity CSV columns. Missing metrics stay missing. Preview is a pure
function: it performs no database or filesystem write.
"""

from __future__ import annotations

import csv
import hashlib
import io
import re
import zipfile
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from decimal import Decimal, InvalidOperation
from pathlib import Path, PurePosixPath
from typing import Any, Final, Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from garmin_fit_sdk import Decoder, Profile, Stream

from healthcurve.events.timekeeping import EventTime, resolve_event_time
from healthcurve.integrations.garmin.models import GarminMetricType

MAX_UPLOAD_BYTES: Final = 25 * 1024 * 1024
MAX_ARCHIVE_MEMBERS: Final = 500
MAX_EXPANDED_BYTES: Final = 100 * 1024 * 1024
MAX_COMPRESSION_RATIO: Final = 100
MAX_CANDIDATES: Final = 100_000
MAX_CSV_ROWS: Final = 50_000
MAX_ARCHIVE_DEPTH: Final = 2
METERS_PER_MILE: Final = Decimal("1609.344")

_PROFILE_VERSION: Any = Profile["version"]
SDK_PROFILE_VERSION: Final = (
    f"{_PROFILE_VERSION['major']}.{_PROFILE_VERSION['minor']}.{_PROFILE_VERSION['patch']}"
)

SUPPORTED_METRICS: Final[frozenset[str]] = frozenset(
    {
        "activity",
        "body_battery",
        "heart_rate",
        "hrv",
        "moderate_intensity_minutes",
        "resting_heart_rate",
        "sleep",
        "sleep_score",
        "steps",
        "stress",
        "vigorous_intensity_minutes",
    }
)


class GarminImportError(ValueError):
    """A privacy-safe validation code suitable for an API response."""


@dataclass(frozen=True)
class DeviceAttribution:
    manufacturer: str = "Garmin"
    product_name: str | None = None
    serial_hash: str | None = None

    def as_dict(self) -> dict[str, str | None]:
        return {
            "manufacturer": self.manufacturer,
            "product_name": self.product_name,
            "serial_hash": self.serial_hash,
        }


@dataclass(frozen=True)
class CandidateSource:
    member_name: str
    member_sha256: str
    device: DeviceAttribution


@dataclass(frozen=True)
class MetricCandidate:
    kind: Literal["metric"] = "metric"
    metric_type: GarminMetricType = GarminMetricType.HEART_RATE
    value: Decimal = Decimal(0)
    unit: str = ""
    field_name: str = ""
    time: EventTime | None = None
    period_end_at: datetime | None = None
    source: CandidateSource | None = None


@dataclass(frozen=True)
class SleepCandidate:
    kind: Literal["sleep"] = "sleep"
    time: EventTime | None = None
    ended_at: datetime | None = None
    overall_sleep_score: int | None = None
    stage_count: int = 0
    source: CandidateSource | None = None


@dataclass(frozen=True)
class ActivityCandidate:
    kind: Literal["activity"] = "activity"
    time: EventTime | None = None
    ended_at: datetime | None = None
    sport: str = ""
    sub_sport: str | None = None
    title: str | None = None
    elapsed_seconds: Decimal | None = None
    distance_miles: Decimal | None = None
    calories: int | None = None
    average_heart_rate: int | None = None
    maximum_heart_rate: int | None = None
    source_notes: str | None = None
    source: CandidateSource | None = None


GarminCandidate = MetricCandidate | SleepCandidate | ActivityCandidate


@dataclass
class ParsedGarminImport:
    source_name: str
    source_media_type: str
    source_sha256: str
    source_payload: bytes = field(repr=False)
    source_members: list[str] = field(default_factory=list)
    candidates: list[GarminCandidate] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    observed_metrics: list[str] = field(default_factory=list)
    missing_metrics: list[str] = field(default_factory=list)
    device_attributions: list[dict[str, str | None]] = field(default_factory=list)
    sdk_profile_version: str = SDK_PROFILE_VERSION


@dataclass(frozen=True)
class _SourcePart:
    name: str
    data: bytes = field(repr=False)


@dataclass
class _ArchiveBudget:
    members: int = 0
    expanded_bytes: int = 0


def parse_upload(filename: str | None, payload: bytes, timezone: str) -> ParsedGarminImport:
    """Parse one bounded upload without persisting its bytes or candidates."""
    if not payload:
        raise GarminImportError("empty_file")
    if len(payload) > MAX_UPLOAD_BYTES:
        raise GarminImportError("file_too_large")
    try:
        ZoneInfo(timezone)
    except ZoneInfoNotFoundError as exc:
        raise GarminImportError("timezone_invalid") from exc

    safe_name = _safe_source_name(filename)
    media_type, parts = _parts(safe_name, payload)
    candidates: list[GarminCandidate] = []
    warnings: list[str] = []
    devices: list[DeviceAttribution] = []

    for part in parts:
        suffix = Path(part.name).suffix.casefold()
        if suffix == ".fit" or _looks_like_fit(part.data):
            parsed, part_warnings, device = _parse_fit(part, timezone)
            devices.append(device)
        elif suffix == ".csv":
            parsed, part_warnings = _parse_activity_csv(part, timezone)
            device = DeviceAttribution()
            devices.append(device)
        else:  # guarded by _parts; defense in depth
            continue
        if len(candidates) + len(parsed) > MAX_CANDIDATES:
            raise GarminImportError("too_many_records")
        candidates.extend(parsed)
        warnings.extend(part_warnings)

    if not parts:
        raise GarminImportError("no_supported_files")
    if not candidates:
        raise GarminImportError("no_supported_records")

    observed_set: set[str] = set()
    for candidate in candidates:
        observed_set.update(_candidate_metrics(candidate))
    observed = sorted(observed_set)
    unique_devices = _unique_devices(devices)
    return ParsedGarminImport(
        source_name=safe_name,
        source_media_type=media_type,
        source_sha256=hashlib.sha256(payload).hexdigest(),
        source_payload=payload,
        source_members=[part.name for part in parts],
        candidates=candidates,
        warnings=sorted(set(warnings)),
        observed_metrics=observed,
        missing_metrics=sorted(SUPPORTED_METRICS - set(observed)),
        device_attributions=[device.as_dict() for device in unique_devices],
    )


def _parts(name: str, payload: bytes) -> tuple[str, list[_SourcePart]]:
    suffix = Path(name).suffix.casefold()
    if suffix == ".zip" or zipfile.is_zipfile(io.BytesIO(payload)):
        return "application/zip", _archive_parts(
            payload, depth=1, prefix="", budget=_ArchiveBudget()
        )
    if suffix == ".fit" or _looks_like_fit(payload):
        return "application/vnd.ant.fit", [_SourcePart(name=name, data=payload)]
    if suffix == ".csv":
        return "text/csv", [_SourcePart(name=name, data=payload)]
    raise GarminImportError("file_type_unsupported")


def _archive_parts(
    payload: bytes, *, depth: int, prefix: str, budget: _ArchiveBudget
) -> list[_SourcePart]:
    if depth > MAX_ARCHIVE_DEPTH:
        raise GarminImportError("archive_nested_too_deep")
    parts: list[_SourcePart] = []
    try:
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            infos = archive.infolist()
            for info in infos:
                if info.is_dir():
                    continue
                budget.members += 1
                if budget.members > MAX_ARCHIVE_MEMBERS:
                    raise GarminImportError("archive_too_many_members")
                member = _safe_member_name(info.filename)
                budget.expanded_bytes += info.file_size
                if budget.expanded_bytes > MAX_EXPANDED_BYTES or info.file_size > MAX_UPLOAD_BYTES:
                    raise GarminImportError("archive_expanded_too_large")
                if info.flag_bits & 0x1:
                    raise GarminImportError("archive_encrypted")
                if info.file_size and (
                    not info.compress_size
                    or info.file_size / info.compress_size > MAX_COMPRESSION_RATIO
                ):
                    raise GarminImportError("archive_compression_ratio_unsafe")
                suffix = Path(member).suffix.casefold()
                if suffix not in {".fit", ".csv", ".zip"}:
                    continue
                data = archive.read(info)
                qualified = f"{prefix}{member}"
                if len(qualified) > 500:
                    raise GarminImportError("archive_member_name_too_long")
                if suffix == ".zip" or zipfile.is_zipfile(io.BytesIO(data)):
                    parts.extend(
                        _archive_parts(
                            data,
                            depth=depth + 1,
                            prefix=f"{qualified}!/",
                            budget=budget,
                        )
                    )
                else:
                    parts.append(_SourcePart(name=qualified, data=data))
    except (zipfile.BadZipFile, RuntimeError) as exc:
        raise GarminImportError("archive_invalid") from exc
    return parts


def _parse_fit(
    part: _SourcePart, timezone: str
) -> tuple[list[GarminCandidate], list[str], DeviceAttribution]:
    if not _looks_like_fit(part.data):
        raise GarminImportError("fit_signature_invalid")
    if not Decoder(Stream.from_byte_array(bytearray(part.data))).check_integrity():
        raise GarminImportError("fit_integrity_invalid")
    messages, errors = Decoder(Stream.from_byte_array(bytearray(part.data))).read(
        enable_crc_check=True
    )
    if errors:
        raise GarminImportError("fit_decode_failed")
    device = _fit_device(messages)
    source = CandidateSource(
        member_name=part.name,
        member_sha256=hashlib.sha256(part.data).hexdigest(),
        device=device,
    )
    candidates: list[GarminCandidate] = []
    warnings: list[str] = []

    for message in messages.get("record_mesgs", []):
        _metric_from_message(
            candidates,
            warnings,
            message,
            timestamp_field="timestamp",
            value_field="heart_rate",
            metric_type=GarminMetricType.HEART_RATE,
            unit="bpm",
            source=source,
            timezone=timezone,
            minimum=20,
            maximum=260,
        )
    for message in messages.get("monitoring_mesgs", []):
        for field_name, metric_type, unit, lower, upper in (
            ("heart_rate", GarminMetricType.HEART_RATE, "bpm", 20, 260),
            (
                "moderate_activity_minutes",
                GarminMetricType.MODERATE_INTENSITY_MINUTES,
                "minutes",
                0,
                1440,
            ),
            (
                "vigorous_activity_minutes",
                GarminMetricType.VIGOROUS_INTENSITY_MINUTES,
                "minutes",
                0,
                1440,
            ),
        ):
            _metric_from_message(
                candidates,
                warnings,
                message,
                timestamp_field="timestamp",
                value_field=field_name,
                metric_type=metric_type,
                unit=unit,
                source=source,
                timezone=timezone,
                minimum=lower,
                maximum=upper,
            )
    for message in messages.get("monitoring_hr_data_mesgs", []):
        _metric_from_message(
            candidates,
            warnings,
            message,
            timestamp_field="timestamp",
            value_field="resting_heart_rate",
            metric_type=GarminMetricType.RESTING_HEART_RATE,
            unit="bpm",
            source=source,
            timezone=timezone,
            minimum=20,
            maximum=260,
        )
    for message in messages.get("stress_level_mesgs", []):
        _metric_from_message(
            candidates,
            warnings,
            message,
            timestamp_field="stress_level_time",
            value_field="stress_level_value",
            metric_type=GarminMetricType.STRESS,
            unit="garmin_score",
            source=source,
            timezone=timezone,
            minimum=0,
            maximum=100,
        )
    for message in messages.get("hrv_value_mesgs", []):
        _metric_from_message(
            candidates,
            warnings,
            message,
            timestamp_field="timestamp",
            value_field="value",
            metric_type=GarminMetricType.HRV,
            unit="ms",
            source=source,
            timezone=timezone,
            minimum=0,
            maximum=1000,
        )
    for message in messages.get("hrv_status_summary_mesgs", []):
        _metric_from_message(
            candidates,
            warnings,
            message,
            timestamp_field="timestamp",
            value_field="last_night_average",
            metric_type=GarminMetricType.HRV,
            unit="ms",
            source=source,
            timezone=timezone,
            minimum=0,
            maximum=1000,
        )

    for key, value_field, metric_type, unit, lower, upper in (
        ("hsa_heart_rate_data_mesgs", "heart_rate", GarminMetricType.HEART_RATE, "bpm", 20, 260),
        ("hsa_stress_data_mesgs", "stress_level", GarminMetricType.STRESS, "garmin_score", 0, 100),
        (
            "hsa_body_battery_data_mesgs",
            "level",
            GarminMetricType.BODY_BATTERY,
            "garmin_score",
            0,
            100,
        ),
        ("hsa_step_data_mesgs", "steps", GarminMetricType.STEPS, "steps", 0, 1_000_000),
    ):
        for message in messages.get(key, []):
            _metric_from_message(
                candidates,
                warnings,
                message,
                timestamp_field="timestamp",
                value_field=value_field,
                metric_type=metric_type,
                unit=unit,
                source=source,
                timezone=timezone,
                minimum=lower,
                maximum=upper,
                duration_field="processing_interval",
            )

    _fit_sleep(candidates, warnings, messages, source, timezone)
    _fit_activities(candidates, warnings, messages, source, timezone)
    return candidates, warnings, device


def _metric_from_message(
    candidates: list[GarminCandidate],
    warnings: list[str],
    message: dict[str, Any],
    *,
    timestamp_field: str,
    value_field: str,
    metric_type: GarminMetricType,
    unit: str,
    source: CandidateSource,
    timezone: str,
    minimum: int,
    maximum: int,
    duration_field: str | None = None,
) -> None:
    if value_field not in message:
        return
    timestamp = message.get(timestamp_field)
    if timestamp is None:
        warnings.append(f"{value_field}:timestamp_missing")
        return
    try:
        value = Decimal(str(message[value_field]))
    except InvalidOperation:
        warnings.append(f"{value_field}:value_invalid")
        return
    if not Decimal(minimum) <= value <= Decimal(maximum):
        warnings.append(f"{value_field}:value_out_of_range")
        return
    event_time = _event_time(timestamp, timezone)
    period_end = None
    if duration_field and message.get(duration_field) is not None:
        try:
            duration = Decimal(str(message[duration_field]))
            if 0 <= duration <= 86_400:
                period_end = event_time.occurred_at + timedelta(seconds=float(duration))
        except (InvalidOperation, ValueError, OverflowError):
            warnings.append(f"{duration_field}:value_invalid")
    _append_candidate(
        candidates,
        MetricCandidate(
            metric_type=metric_type,
            value=value,
            unit=unit,
            field_name=value_field,
            time=event_time,
            period_end_at=period_end,
            source=source,
        ),
    )


def _fit_sleep(
    candidates: list[GarminCandidate],
    warnings: list[str],
    messages: dict[str, list[dict[str, Any]]],
    source: CandidateSource,
    timezone: str,
) -> None:
    timestamps = [message.get("timestamp") for message in messages.get("sleep_level_mesgs", [])]
    explicit = sorted(_as_datetime(value) for value in timestamps if value is not None)
    if not explicit:
        return
    if len(explicit) < 2 or explicit[-1] <= explicit[0]:
        warnings.append("sleep:explicit_bounds_missing")
        return
    score: int | None = None
    assessments = messages.get("sleep_assessment_mesgs", [])
    if assessments and assessments[-1].get("overall_sleep_score") is not None:
        possible = int(assessments[-1]["overall_sleep_score"])
        if 0 <= possible <= 100:
            score = possible
        else:
            warnings.append("overall_sleep_score:value_out_of_range")
    _append_candidate(
        candidates,
        SleepCandidate(
            time=_event_time(explicit[0], timezone),
            ended_at=_event_time(explicit[-1], timezone).occurred_at,
            overall_sleep_score=score,
            stage_count=len(explicit),
            source=source,
        ),
    )


def _fit_activities(
    candidates: list[GarminCandidate],
    warnings: list[str],
    messages: dict[str, list[dict[str, Any]]],
    source: CandidateSource,
    timezone: str,
) -> None:
    for message in messages.get("session_mesgs", []):
        start = message.get("start_time")
        sport = message.get("sport")
        if start is None or sport is None:
            warnings.append("activity:required_field_missing")
            continue
        start_time = _event_time(start, timezone)
        elapsed = _optional_decimal(message.get("total_elapsed_time"))
        end_value = message.get("timestamp")
        if end_value is not None:
            ended_at = _event_time(end_value, timezone).occurred_at
        elif elapsed is not None:
            ended_at = start_time.occurred_at + timedelta(seconds=float(elapsed))
        else:
            warnings.append("activity:end_missing")
            continue
        _append_candidate(
            candidates,
            ActivityCandidate(
                time=start_time,
                ended_at=ended_at,
                sport=str(sport),
                sub_sport=_optional_text(message.get("sub_sport")),
                title=_optional_text(message.get("sport_profile_name")),
                elapsed_seconds=elapsed,
                distance_miles=_meters_to_miles(_optional_decimal(message.get("total_distance"))),
                calories=_bounded_int(message.get("total_calories"), 0, 1_000_000),
                average_heart_rate=_bounded_int(message.get("avg_heart_rate"), 20, 260),
                maximum_heart_rate=_bounded_int(message.get("max_heart_rate"), 20, 260),
                source=source,
            ),
        )
        for field_name, metric_type, upper in (
            ("rmssd_hrv", GarminMetricType.HRV, 1000),
            ("sdrr_hrv", GarminMetricType.HRV, 1000),
            ("avg_stress", GarminMetricType.STRESS, 100),
        ):
            if message.get(field_name) is not None:
                _metric_from_message(
                    candidates,
                    warnings,
                    message,
                    timestamp_field="timestamp",
                    value_field=field_name,
                    metric_type=metric_type,
                    unit="ms" if metric_type is GarminMetricType.HRV else "garmin_score",
                    source=source,
                    timezone=timezone,
                    minimum=0,
                    maximum=upper,
                )


def _parse_activity_csv(
    part: _SourcePart, timezone: str
) -> tuple[list[GarminCandidate], list[str]]:
    try:
        text = part.data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise GarminImportError("csv_encoding_invalid") from exc
    reader = csv.DictReader(io.StringIO(text))
    if not reader.fieldnames:
        raise GarminImportError("csv_header_missing")
    headers = {_normal_header(header): header for header in reader.fieldnames if header}
    date_header = _header(headers, "date", "start_time", "start_date")
    sport_header = _header(headers, "activity_type", "activity", "sport")
    if date_header is None or sport_header is None:
        raise GarminImportError("csv_schema_unsupported")
    title_header = _header(headers, "title", "activity_name")
    duration_header = _header(headers, "time", "duration", "elapsed_time")
    calories_header = _header(headers, "calories")
    average_hr_header = _header(headers, "avg_hr", "average_hr", "average_heart_rate")
    maximum_hr_header = _header(headers, "max_hr", "maximum_hr", "maximum_heart_rate")
    distance_header, distance_factor = _distance_header(headers)
    warnings: list[str] = []
    if _header(headers, "distance") and distance_header is None:
        warnings.append("distance:unit_missing")

    source = CandidateSource(
        member_name=part.name,
        member_sha256=hashlib.sha256(part.data).hexdigest(),
        device=DeviceAttribution(),
    )
    candidates: list[GarminCandidate] = []
    for index, row in enumerate(reader, start=1):
        if index > MAX_CSV_ROWS:
            raise GarminImportError("csv_too_many_rows")
        try:
            start = _parse_csv_datetime(row.get(date_header, ""), timezone)
        except GarminImportError:
            warnings.append("activity_csv:date_invalid")
            continue
        sport = (row.get(sport_header) or "").strip()
        if not sport:
            warnings.append("activity_csv:sport_missing")
            continue
        elapsed = _parse_duration(row.get(duration_header, "")) if duration_header else None
        ended_at = start.occurred_at + timedelta(seconds=float(elapsed or 0))
        parsed_distance = _optional_decimal(row.get(distance_header)) if distance_header else None
        distance = parsed_distance * distance_factor if parsed_distance is not None else None
        _append_candidate(
            candidates,
            ActivityCandidate(
                time=start,
                ended_at=ended_at,
                sport=sport.casefold().replace(" ", "_"),
                title=_optional_text(row.get(title_header)) if title_header else None,
                elapsed_seconds=elapsed,
                distance_miles=distance,
                calories=(
                    _bounded_int(_clean_number(row.get(calories_header)), 0, 1_000_000)
                    if calories_header
                    else None
                ),
                average_heart_rate=(
                    _bounded_int(_clean_number(row.get(average_hr_header)), 20, 260)
                    if average_hr_header
                    else None
                ),
                maximum_heart_rate=(
                    _bounded_int(_clean_number(row.get(maximum_hr_header)), 20, 260)
                    if maximum_hr_header
                    else None
                ),
                source=source,
            ),
        )
    return candidates, warnings


def _fit_device(messages: dict[str, list[dict[str, Any]]]) -> DeviceAttribution:
    possible = messages.get("device_info_mesgs", []) or messages.get("file_id_mesgs", [])
    message = possible[0] if possible else {}
    manufacturer = _optional_text(message.get("manufacturer")) or "Garmin"
    product = _optional_text(message.get("product_name"))
    if product is None and message.get("garmin_product") is not None:
        product = str(message["garmin_product"])
    serial = message.get("serial_number")
    serial_hash = (
        hashlib.sha256(f"healthcurve:garmin-device:{serial}".encode()).hexdigest()
        if serial not in (None, 0, "0")
        else None
    )
    return DeviceAttribution(
        manufacturer=manufacturer,
        product_name=product,
        serial_hash=serial_hash,
    )


def _event_time(value: Any, timezone: str) -> EventTime:
    parsed = _as_datetime(value)
    if parsed.tzinfo is None:
        result = resolve_event_time(parsed, timezone)
    else:
        zone = ZoneInfo(timezone)
        occurred = parsed.astimezone(UTC)
        local = occurred.astimezone(zone)
        offset = local.utcoffset()
        assert offset is not None
        result = EventTime(
            occurred_at=occurred,
            local_time=local.replace(tzinfo=None),
            timezone=timezone,
            utc_offset_minutes=int(offset.total_seconds() // 60),
        )
    # The fact schema requires recorded_at >= occurred_at. Accepting clock-skewed
    # future data would either violate that invariant or force us to invent a future
    # recorded_at, so fail explicitly instead.
    if result.occurred_at > datetime.now(UTC):
        raise GarminImportError("timestamp_in_future")
    return result


def _as_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError as exc:
            raise GarminImportError("timestamp_invalid") from exc
    raise GarminImportError("timestamp_invalid")


def _parse_csv_datetime(value: str, timezone: str) -> EventTime:
    cleaned = value.strip()
    if not cleaned:
        raise GarminImportError("timestamp_invalid")
    patterns = (
        None,
        "%Y-%m-%d %H:%M:%S",
        "%m/%d/%Y %H:%M",
        "%m/%d/%Y %I:%M %p",
    )
    for pattern in patterns:
        try:
            # strptime intentionally returns local wall time; resolve_event_time
            # applies the owner-selected zone and rejects DST ambiguity/gaps.
            parsed = (
                datetime.fromisoformat(cleaned)
                if pattern is None
                else datetime.strptime(cleaned, pattern)  # noqa: DTZ007
            )
            return _event_time(parsed, timezone)
        except ValueError:
            continue
    raise GarminImportError("timestamp_invalid")


def _parse_duration(value: str | None) -> Decimal | None:
    if value is None or not value.strip():
        return None
    cleaned = value.strip()
    parts = cleaned.split(":")
    try:
        if len(parts) == 3:
            hours, minutes, seconds = (Decimal(part) for part in parts)
            result = hours * 3600 + minutes * 60 + seconds
        else:
            result = Decimal(cleaned)
    except InvalidOperation:
        return None
    return result if 0 <= result <= Decimal(60 * 60 * 24 * 31) else None


def _distance_header(headers: dict[str, str]) -> tuple[str | None, Decimal]:
    for normalized, original in headers.items():
        if not normalized.startswith("distance"):
            continue
        if normalized in {"distance_km", "distance_kilometers"}:
            return original, Decimal(1000) / METERS_PER_MILE
        if normalized in {"distance_mi", "distance_miles"}:
            return original, Decimal(1)
        if normalized in {"distance_m", "distance_meters"}:
            return original, Decimal(1) / METERS_PER_MILE
    return None, Decimal(1)


def _meters_to_miles(value: Decimal | None) -> Decimal | None:
    return None if value is None else value / METERS_PER_MILE


def _normal_header(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", value.strip().casefold()).strip("_")


def _header(headers: dict[str, str], *names: str) -> str | None:
    return next((headers[name] for name in names if name in headers), None)


def _safe_source_name(filename: str | None) -> str:
    name = Path(filename or "garmin-upload").name.strip()
    name = re.sub(r"[\x00-\x1f\x7f]+", "_", name)
    if not name:
        name = "garmin-upload"
    return name[:255]


def _safe_member_name(value: str) -> str:
    if "\\" in value or re.search(r"[\x00-\x1f\x7f]", value):
        raise GarminImportError("archive_member_path_unsafe")
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.name:
        raise GarminImportError("archive_member_path_unsafe")
    return str(path)


def _looks_like_fit(payload: bytes) -> bool:
    return len(payload) >= 12 and payload[8:12] == b".FIT"


def _candidate_metrics(candidate: GarminCandidate) -> set[str]:
    if isinstance(candidate, ActivityCandidate):
        return {"activity"}
    if isinstance(candidate, SleepCandidate):
        result = {"sleep"}
        if candidate.overall_sleep_score is not None:
            result.add("sleep_score")
        return result
    return {candidate.metric_type.value}


def _unique_devices(devices: list[DeviceAttribution]) -> list[DeviceAttribution]:
    result: list[DeviceAttribution] = []
    seen: set[tuple[str, str | None, str | None]] = set()
    for device in devices:
        key = (device.manufacturer, device.product_name, device.serial_hash)
        if key not in seen:
            seen.add(key)
            result.append(device)
    return result


def _optional_decimal(value: Any) -> Decimal | None:
    if value in (None, ""):
        return None
    try:
        parsed = Decimal(str(value).replace(",", ""))
    except InvalidOperation:
        return None
    return parsed if parsed >= 0 else None


def _clean_number(value: str | None) -> str | None:
    return None if value is None else value.replace(",", "").strip()


def _bounded_int(value: Any, minimum: int, maximum: int) -> int | None:
    if value in (None, ""):
        return None
    try:
        parsed = int(Decimal(str(value)))
    except (InvalidOperation, ValueError):
        return None
    return parsed if minimum <= parsed <= maximum else None


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _append_candidate(candidates: list[GarminCandidate], candidate: GarminCandidate) -> None:
    if len(candidates) >= MAX_CANDIDATES:
        raise GarminImportError("too_many_records")
    candidates.append(candidate)
