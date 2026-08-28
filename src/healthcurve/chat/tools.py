"""Bounded, owner-scoped, read-only data tools for the private chatbot.

Every public entry point in this module accepts the authenticated owner separately
from model-provided arguments. Tool arguments contain no owner ID, SQL, table name, or
arbitrary filter expression. PostgreSQL transactions are switched to read-only before
the first domain query as defence in depth.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections.abc import Callable
from datetime import UTC, date, datetime, time, timedelta
from decimal import Decimal
from typing import Any, Final, Literal, cast
from zoneinfo import ZoneInfo

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, model_validator
from pydantic_core import to_jsonable_python
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from healthcurve.analytics import day_analysis, injectable_pharmacokinetics, wake_reference_inputs
from healthcurve.analytics import service as analytics_service
from healthcurve.context.models import ContextEvent
from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events import service as event_service
from healthcurve.events.base import EventMixin
from healthcurve.events.models import DiaryEvent, LifeEvent, MealEvent, SymptomEvent
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminMetricEvent,
    GarminMetricType,
    GarminSleepEvent,
    WearableDailySummary,
)
from healthcurve.labs.models import LabPanel, LabResult
from healthcurve.medications import service as medication_service
from healthcurve.medications.models import DoseEvent, RegimenStatus, RegimenVersion
from healthcurve.reports.models import ReportSnapshot
from healthcurve.vitals.models import BloodPressureEvent, TemperatureEvent, WeightEvent

CHAT_TOOL_CATALOG_VERSION: Final = "hc-chat-tools-v2"
MAX_RANGE_DAYS: Final = 366
DEFAULT_RANGE_DAYS: Final = 30
MAX_SPARSE_ROWS: Final = 200
DEFAULT_SPARSE_ROWS: Final = 50
MAX_DENSE_BUCKETS: Final = 2_000
DEFAULT_BUCKET_MINUTES: Final = 60
ALLOWED_BUCKET_MINUTES: Final = frozenset({15, 30, 60, 120, 240})


class ChatToolError(ValueError):
    """A typed, non-sensitive tool error safe to expose as a reason code."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class ToolArguments(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class DateRangeArguments(ToolArguments):
    date_from: date
    date_to: date
    timezone: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_range(self) -> DateRangeArguments:
        if self.date_to < self.date_from:
            raise ValueError("date_to must be on or after date_from")
        if (self.date_to - self.date_from).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"date range cannot exceed {MAX_RANGE_DAYS} days")
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return self


class DataAvailabilityArguments(DateRangeArguments):
    pass


class DailyHealthCurveArguments(ToolArguments):
    day: date
    timezone: str = Field(min_length=1, max_length=64)
    include_sensitive_text: bool = False

    @model_validator(mode="after")
    def valid_timezone(self) -> DailyHealthCurveArguments:
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return self


TimelineRecordType = Literal[
    "dose",
    "symptom",
    "meal",
    "diary",
    "life_event",
    "blood_pressure",
    "weight",
    "temperature",
    "garmin_metric",
    "garmin_sleep",
    "garmin_activity",
    "emergency_injection",
]


class TimelineArguments(DateRangeArguments):
    record_types: list[TimelineRecordType] = Field(default_factory=list, max_length=12)
    limit: int = Field(default=DEFAULT_SPARSE_ROWS, ge=1, le=MAX_SPARSE_ROWS)
    include_sensitive_text: bool = False


class MedicationContextArguments(DateRangeArguments):
    limit: int = Field(default=DEFAULT_SPARSE_ROWS, ge=1, le=MAX_SPARSE_ROWS)


class SymptomEpisodeArguments(DateRangeArguments):
    limit: int = Field(default=DEFAULT_SPARSE_ROWS, ge=1, le=MAX_SPARSE_ROWS)


class WearableArguments(DateRangeArguments):
    metrics: list[GarminMetricType] = Field(default_factory=list, max_length=9)
    include_intraday: bool = False
    bucket_minutes: int = DEFAULT_BUCKET_MINUTES

    @model_validator(mode="after")
    def valid_bucket(self) -> WearableArguments:
        if self.bucket_minutes not in ALLOWED_BUCKET_MINUTES:
            raise ValueError("bucket_minutes must be one of 15, 30, 60, 120, or 240")
        day_count = (self.date_to - self.date_from).days + 1
        buckets_per_day = 1_440 // self.bucket_minutes
        metric_count = len(self.metrics) or len(GarminMetricType)
        if self.include_intraday and day_count * buckets_per_day * metric_count > MAX_DENSE_BUCKETS:
            raise ValueError(f"intraday request cannot exceed {MAX_DENSE_BUCKETS} buckets")
        return self


class PrecedingHealthContextArguments(ToolArguments):
    anchor_at: datetime
    timezone: str = Field(min_length=1, max_length=64)
    lookback_hours: int = Field(default=6, ge=1, le=24)
    history_days: int = Field(default=90, ge=7, le=366)
    symptom_name: str | None = Field(default=None, min_length=1, max_length=200)
    similar_limit: int = Field(default=8, ge=1, le=24)
    include_stress_episode_anchors: bool = False

    @model_validator(mode="after")
    def valid_anchor(self) -> PrecedingHealthContextArguments:
        if self.anchor_at.utcoffset() is None:
            raise ValueError("anchor_at must include a UTC offset")
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return self


class LabTrendArguments(DateRangeArguments):
    analytes: list[str] = Field(default_factory=list, max_length=20)
    limit: int = Field(default=DEFAULT_SPARSE_ROWS, ge=1, le=MAX_SPARSE_ROWS)


class Period(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    date_from: date
    date_to: date

    @model_validator(mode="after")
    def valid_period(self) -> Period:
        if self.date_to < self.date_from:
            raise ValueError("period date_to must be on or after date_from")
        if (self.date_to - self.date_from).days + 1 > MAX_RANGE_DAYS:
            raise ValueError(f"period cannot exceed {MAX_RANGE_DAYS} days")
        return self


class ComparePeriodsArguments(ToolArguments):
    first: Period
    second: Period
    timezone: str = Field(min_length=1, max_length=64)

    @model_validator(mode="after")
    def valid_timezone(self) -> ComparePeriodsArguments:
        try:
            ZoneInfo(self.timezone)
        except Exception as exc:
            raise ValueError("timezone must be a valid IANA timezone") from exc
        return self


class ReportSnapshotArguments(ToolArguments):
    snapshot_id: uuid.UUID
    include_ai_section: bool = False


class ChatToolResult(BaseModel):
    """Validated tool output passed to the model and source validator."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    tool_name: str
    tool_version: str = CHAT_TOOL_CATALOG_VERSION
    timezone: str | None = None
    date_scope: dict[str, str] | None = None
    data: dict[str, Any]
    missingness: dict[str, Any]
    source_manifest: dict[str, list[str]]
    result_sha256: str


_ARGUMENT_MODELS: Final[dict[str, type[ToolArguments]]] = {
    "get_data_availability": DataAvailabilityArguments,
    "get_daily_healthcurve": DailyHealthCurveArguments,
    "search_timeline": TimelineArguments,
    "get_medication_context": MedicationContextArguments,
    "get_symptom_episode_context": SymptomEpisodeArguments,
    "get_wearable_context": WearableArguments,
    "get_preceding_health_context": PrecedingHealthContextArguments,
    "get_lab_trends": LabTrendArguments,
    "compare_periods": ComparePeriodsArguments,
    "get_report_snapshot_context": ReportSnapshotArguments,
}


def tool_definitions() -> list[dict[str, object]]:
    """Return model-safe JSON Schemas for the fixed tool allow-list."""
    descriptions = {
        "get_data_availability": "Count available and missing HealthCurve domains in a range.",
        "get_daily_healthcurve": "Read the fingerprinted deterministic projection for one day.",
        "search_timeline": "Read a bounded list of current recorded facts in a date range.",
        "get_medication_context": "Read approved plans and actual recorded doses separately.",
        "get_symptom_episode_context": "Read current symptoms and overlapping stress episodes.",
        "get_wearable_context": "Read Garmin summaries and optionally bounded intraday buckets.",
        "get_preceding_health_context": (
            "Read a bounded preceding-hours view with modeled-curve position, recorded "
            "events, weather, sleep, wearable comparisons, and prior symptom contexts."
        ),
        "get_lab_trends": "Read bounded lab results preserving original units and ranges.",
        "compare_periods": "Compute deterministic descriptive metrics for two periods.",
        "get_report_snapshot_context": "Read one owner-selected immutable report snapshot.",
    }
    return [
        {
            "name": name,
            "description": descriptions[name],
            "input_schema": model.model_json_schema(),
        }
        for name, model in _ARGUMENT_MODELS.items()
    ]


def _jsonable(value: object) -> Any:
    return to_jsonable_python(value, serialize_unknown=True, fallback=str)


def _canonical_sha(value: object) -> str:
    encoded = json.dumps(_jsonable(value), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def _date_scope(date_from: date, date_to: date) -> dict[str, str]:
    return {"date_from": date_from.isoformat(), "date_to": date_to.isoformat()}


def _bounds(args: DateRangeArguments) -> tuple[datetime, datetime]:
    zone = ZoneInfo(args.timezone)
    return (
        datetime.combine(args.date_from, time.min, tzinfo=zone).astimezone(UTC),
        datetime.combine(args.date_to + timedelta(days=1), time.min, tzinfo=zone).astimezone(UTC),
    )


def _read_only(session: Session) -> None:
    """Make PostgreSQL enforce read-only behavior for the whole tool transaction."""
    if session.get_bind().dialect.name == "postgresql":
        session.execute(text("SET TRANSACTION READ ONLY"))


def _current_rows(
    session: Session,
    model: type[EventMixin],
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[EventMixin]:
    statement = (
        select(model)
        .where(
            model.owner_id == owner_id,
            model.occurred_at >= start,
            model.occurred_at < end,
            event_service.current_fact_predicate(model, owner_id=owner_id),
        )
        .order_by(model.occurred_at.desc(), model.id)
        .limit(limit)
    )
    return list(session.scalars(statement))


def _current_row_count(
    session: Session,
    model: type[EventMixin],
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> int:
    return int(
        session.scalar(
            select(func.count(model.id)).where(
                model.owner_id == owner_id,
                model.occurred_at >= start,
                model.occurred_at < end,
                event_service.current_fact_predicate(model, owner_id=owner_id),
            )
        )
        or 0
    )


def _current_sleep_rows_by_wake(
    session: Session,
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
    limit: int,
) -> list[GarminSleepEvent]:
    """Return current sleep facts assigned to the local date of their wake time."""
    statement = (
        select(GarminSleepEvent)
        .where(
            GarminSleepEvent.owner_id == owner_id,
            GarminSleepEvent.ended_at >= start,
            GarminSleepEvent.ended_at < end,
            event_service.current_fact_predicate(GarminSleepEvent, owner_id=owner_id),
        )
        .order_by(GarminSleepEvent.ended_at.desc(), GarminSleepEvent.id)
        .limit(limit)
    )
    return list(session.scalars(statement))


def _current_sleep_count_by_wake(
    session: Session,
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> int:
    return int(
        session.scalar(
            select(func.count(GarminSleepEvent.id)).where(
                GarminSleepEvent.owner_id == owner_id,
                GarminSleepEvent.ended_at >= start,
                GarminSleepEvent.ended_at < end,
                event_service.current_fact_predicate(GarminSleepEvent, owner_id=owner_id),
            )
        )
        or 0
    )


def _average_local_time(instants: list[datetime], *, timezone: str) -> str | None:
    """Return a circular mean clock time so values around midnight remain adjacent."""
    if not instants:
        return None
    zone = ZoneInfo(timezone)
    angles = [
        2.0
        * math.pi
        * (
            instant.astimezone(zone).hour * 3_600
            + instant.astimezone(zone).minute * 60
            + instant.astimezone(zone).second
        )
        / 86_400.0
        for instant in instants
    ]
    angle = math.atan2(
        sum(math.sin(item) for item in angles),
        sum(math.cos(item) for item in angles),
    )
    seconds = round((angle % (2.0 * math.pi)) * 86_400.0 / (2.0 * math.pi) / 60.0) * 60
    return f"{(seconds // 3_600) % 24:02d}:{(seconds % 3_600) // 60:02d}"


def _event_base(row: EventMixin) -> dict[str, object]:
    return {
        "id": row.id,
        "occurred_at": row.occurred_at,
        "local_time": row.local_time,
        "timezone": row.timezone,
        "source_type": row.source_type,
        "confirmation_state": row.confirmation_state,
        "source_revision": row.source_revision,
    }


def _event_payload(row: EventMixin, *, include_sensitive: bool) -> dict[str, object]:
    payload = _event_base(row)
    if isinstance(row, DoseEvent):
        payload.update(
            record_type="dose",
            medication_name=row.medication.name,
            amount=row.amount,
            unit=row.unit,
            route=row.route,
            dose_category=row.category,
            plan_version_id=row.regimen_version_id,
            episode_id=row.episode_id,
        )
    elif isinstance(row, SymptomEvent):
        payload.update(
            record_type="symptom",
            name=row.name,
            severity_0_to_10=row.severity,
            body_area=row.body_area,
            ended_at=row.ended_at,
            episode_id=row.episode_id,
            notes=row.notes,
        )
    elif isinstance(row, MealEvent):
        payload.update(
            record_type="meal",
            size=row.size,
            notes=row.notes,
        )
    elif isinstance(row, DiaryEvent):
        payload.update(
            record_type="diary",
            is_sensitive=row.is_sensitive,
            tags=row.tags,
            text=(
                row.text
                if include_sensitive or not row.is_sensitive
                else "[sensitive text withheld]"
            ),
        )
    elif isinstance(row, LifeEvent):
        payload.update(
            record_type="life_event",
            title=row.title,
            category=row.category,
            ended_at=row.ended_at,
            is_sensitive=row.is_sensitive,
            description=(
                row.description
                if include_sensitive or not row.is_sensitive
                else "[sensitive text withheld]"
            ),
        )
    elif isinstance(row, BloodPressureEvent):
        payload.update(
            record_type="blood_pressure",
            systolic_mmhg=row.systolic_mmhg,
            diastolic_mmhg=row.diastolic_mmhg,
            pulse_bpm=row.pulse_bpm,
            measurement_setting=row.measurement_setting,
        )
    elif isinstance(row, WeightEvent):
        payload.update(
            record_type="weight",
            value=row.value,
            unit=row.unit,
            normalized_kg=row.normalized_kg,
            measurement_setting=row.measurement_setting,
        )
    elif isinstance(row, TemperatureEvent):
        payload.update(
            record_type="temperature",
            value=row.value,
            unit=row.unit,
            normalized_c=row.normalized_c,
        )
    elif isinstance(row, GarminMetricEvent):
        payload.update(
            record_type="garmin_metric",
            metric_type=row.metric_type,
            value=row.value,
            unit=row.unit,
            aggregation=row.aggregation,
            sample_interval_seconds=row.sample_interval_seconds,
        )
    elif isinstance(row, GarminSleepEvent):
        payload.update(
            record_type="garmin_sleep",
            ended_at=row.ended_at,
            duration_seconds=row.duration_seconds,
            overall_sleep_score=row.overall_sleep_score,
            awakenings=row.awakenings,
        )
    elif isinstance(row, GarminActivityEvent):
        payload.update(
            record_type="garmin_activity",
            ended_at=row.ended_at,
            sport=row.sport,
            title=row.title,
            elapsed_seconds=row.elapsed_seconds,
            distance_miles=row.distance_miles,
            calories=row.calories,
        )
    elif isinstance(row, EmergencyInjectionEvent):
        payload.update(
            record_type="emergency_injection",
            amount=row.amount,
            unit=row.unit,
            route=row.route,
            reason=row.reason,
            response=row.response,
            emergency_services_called=row.emergency_services_called,
            transported_to_hospital=row.transported_to_hospital,
        )
    else:  # pragma: no cover - the allow-list above makes this defensive only
        raise ChatToolError("unsupported_timeline_record")
    return payload


def _result(
    *,
    name: str,
    data: dict[str, object],
    missingness: dict[str, object],
    source_manifest: dict[str, list[str]],
    timezone: str | None = None,
    date_scope: dict[str, str] | None = None,
) -> ChatToolResult:
    body = {
        "tool_name": name,
        "tool_version": CHAT_TOOL_CATALOG_VERSION,
        "timezone": timezone,
        "date_scope": date_scope,
        "data": _jsonable(data),
        "missingness": _jsonable(missingness),
        "source_manifest": source_manifest,
    }
    return ChatToolResult(
        tool_name=name,
        tool_version=CHAT_TOOL_CATALOG_VERSION,
        timezone=timezone,
        date_scope=date_scope,
        data=cast(dict[str, Any], body["data"]),
        missingness=cast(dict[str, Any], body["missingness"]),
        source_manifest=source_manifest,
        result_sha256=_canonical_sha(body),
    )


def _get_daily_healthcurve(
    session: Session, owner_id: uuid.UUID, args: DailyHealthCurveArguments
) -> ChatToolResult:
    projection = day_analysis.build_projection(
        session, owner_id=owner_id, day=args.day, timezone=args.timezone
    )
    facts = cast(dict[str, Any], projection["recorded_facts_and_plan_context"])
    if not args.include_sensitive_text:
        facts["diary"] = [row for row in facts["diary"] if not row["is_sensitive"]]
        facts["life_events"] = [row for row in facts["life_events"] if not row["is_sensitive"]]
    source_ids = cast(list[str], projection.pop("source_record_ids"))
    return _result(
        name="get_daily_healthcurve",
        data={"projection": projection},
        missingness={"missing_domains": projection["missing_domains"]},
        source_manifest={"fact_plan_and_projection": source_ids},
        timezone=args.timezone,
        date_scope=_date_scope(args.day, args.day),
    )


def _search_timeline(
    session: Session, owner_id: uuid.UUID, args: TimelineArguments
) -> ChatToolResult:
    start, end = _bounds(args)
    types = set(args.record_types)
    models: list[tuple[str, type[EventMixin]]] = [
        ("dose", DoseEvent),
        ("symptom", SymptomEvent),
        ("meal", MealEvent),
        ("diary", DiaryEvent),
        ("life_event", LifeEvent),
        ("blood_pressure", BloodPressureEvent),
        ("weight", WeightEvent),
        ("temperature", TemperatureEvent),
        ("garmin_metric", GarminMetricEvent),
        ("garmin_sleep", GarminSleepEvent),
        ("garmin_activity", GarminActivityEvent),
        ("emergency_injection", EmergencyInjectionEvent),
    ]
    selected = [item for item in models if not types or item[0] in types]
    record_counts = {
        name: (
            _current_sleep_count_by_wake(
                session,
                owner_id=owner_id,
                start=start,
                end=end,
            )
            if model is GarminSleepEvent
            else _current_row_count(
                session,
                model,
                owner_id=owner_id,
                start=start,
                end=end,
            )
        )
        for name, model in selected
    }
    rows = [
        row
        for _, model in selected
        for row in (
            _current_sleep_rows_by_wake(
                session,
                owner_id=owner_id,
                start=start,
                end=end,
                limit=args.limit,
            )
            if model is GarminSleepEvent
            else _current_rows(
                session,
                model,
                owner_id=owner_id,
                start=start,
                end=end,
                limit=args.limit,
            )
        )
    ]
    rows.sort(key=lambda row: (row.occurred_at, row.id), reverse=True)
    limited = rows[: args.limit]
    items = [_event_payload(row, include_sensitive=args.include_sensitive_text) for row in limited]
    sleep_rows = [row for row in rows if isinstance(row, GarminSleepEvent)]
    wake_instants = [row.ended_at for row in sleep_rows]
    average_wake_time = _average_local_time(wake_instants, timezone=args.timezone)
    average_wake_hour: int | None = None
    average_wake_minute: int | None = None
    if average_wake_time is not None:
        hour_text, minute_text = average_wake_time.split(":", maxsplit=1)
        average_wake_hour = int(hour_text)
        average_wake_minute = int(minute_text)
    return _result(
        name="search_timeline",
        data={
            "items": items,
            "limit": args.limit,
            "has_more": len(rows) > args.limit,
            "record_counts": record_counts,
            "wake_time_summary": {
                "sample_count": len(wake_instants),
                "average_local_time": average_wake_time,
                "average_local_hour": average_wake_hour,
                "average_local_minute": average_wake_minute,
            },
        },
        missingness={
            "no_matching_records": not items,
            "sensitive_text_included": args.include_sensitive_text,
        },
        source_manifest={"fact": [str(row.id) for row in limited]},
        timezone=args.timezone,
        date_scope=_date_scope(args.date_from, args.date_to),
    )


def _plan_payload(version: RegimenVersion) -> dict[str, object]:
    return {
        "id": version.id,
        "category": "physician_approved_plan",
        "version_label": version.version_label,
        "status": version.status,
        "effective_from": version.effective_from,
        "effective_to": version.effective_to,
        "effective_timezone": version.effective_timezone,
        "approved_at": version.approved_at,
        "approved_by": version.approved_by,
        "approval_source": version.approval_source,
        "slots": [
            {
                "id": slot.id,
                "medication_name": slot.medication.name,
                "timing_mode": slot.timing_mode,
                "scheduled_local_time": slot.scheduled_local_time,
                "reminder_local_time": slot.reminder_local_time,
                "amount": slot.amount,
                "unit": slot.unit,
                "route": slot.route,
                "condition": slot.condition,
            }
            for slot in version.slots
        ],
        "instructions": [
            {
                "id": instruction.id,
                "category": instruction.category,
                "title": instruction.title,
                "body": instruction.body,
                "authored_by": instruction.authored_by,
                "authored_on": instruction.authored_on,
            }
            for instruction in version.instructions
        ],
    }


def _get_medication_context(
    session: Session, owner_id: uuid.UUID, args: MedicationContextArguments
) -> ChatToolResult:
    start, end = _bounds(args)
    plans = medication_service.approved_versions_during(
        session, owner_id=owner_id, start=start, end=end
    )
    doses = cast(
        list[DoseEvent],
        _current_rows(
            session,
            DoseEvent,
            owner_id=owner_id,
            start=start,
            end=end,
            limit=args.limit,
        ),
    )
    plan_ids = [
        str(item_id)
        for version in plans
        for item_id in (
            version.id,
            *(slot.id for slot in version.slots),
            *(instruction.id for instruction in version.instructions),
        )
    ]
    return _result(
        name="get_medication_context",
        data={
            "physician_approved_plans": [_plan_payload(version) for version in plans],
            "recorded_doses": [_event_payload(row, include_sensitive=False) for row in doses],
            "plan_and_record_are_distinct": True,
        },
        missingness={"no_approved_plan": not plans, "no_recorded_doses": not doses},
        source_manifest={"plan": plan_ids, "fact": [str(row.id) for row in doses]},
        timezone=args.timezone,
        date_scope=_date_scope(args.date_from, args.date_to),
    )


def _get_symptom_episode_context(
    session: Session, owner_id: uuid.UUID, args: SymptomEpisodeArguments
) -> ChatToolResult:
    start, end = _bounds(args)
    symptom_count = _current_row_count(
        session,
        SymptomEvent,
        owner_id=owner_id,
        start=start,
        end=end,
    )
    symptoms = cast(
        list[SymptomEvent],
        _current_rows(
            session,
            SymptomEvent,
            owner_id=owner_id,
            start=start,
            end=end,
            limit=args.limit,
        ),
    )
    episodes = list(
        session.scalars(
            select(StressEpisode)
            .where(
                StressEpisode.owner_id == owner_id,
                StressEpisode.started_at < end,
                or_(StressEpisode.ended_at.is_(None), StressEpisode.ended_at > start),
            )
            .order_by(StressEpisode.started_at.desc(), StressEpisode.id)
            .limit(args.limit)
        )
    )
    return _result(
        name="get_symptom_episode_context",
        data={
            "symptom_count": symptom_count,
            "symptoms": [_event_payload(row, include_sensitive=False) for row in symptoms],
            "stress_episodes": [
                {
                    "id": row.id,
                    "trigger": row.trigger,
                    "status": row.status,
                    "severity": row.severity,
                    "started_at": row.started_at,
                    "ended_at": row.ended_at,
                    "timezone": row.timezone,
                    "highest_temperature_c": row.highest_temperature_c,
                    "illness_description": row.illness_description,
                    "recovery_notes": row.recovery_notes,
                    "outcome": row.outcome,
                    "notes": row.notes,
                }
                for row in episodes
            ],
        },
        missingness={
            "no_symptoms": symptom_count == 0,
            "no_overlapping_episodes": not episodes,
            "symptoms_without_severity": sum(row.severity is None for row in symptoms),
            "open_episodes": sum(row.ended_at is None for row in episodes),
        },
        source_manifest={
            "fact": [str(row.id) for row in [*symptoms, *episodes]],
        },
        timezone=args.timezone,
        date_scope=_date_scope(args.date_from, args.date_to),
    )


def _bucket_intraday(
    rows: list[GarminMetricEvent], *, timezone: str, bucket_minutes: int
) -> list[dict[str, object]]:
    zone = ZoneInfo(timezone)
    grouped: dict[tuple[GarminMetricType, str, datetime], list[GarminMetricEvent]] = {}
    for row in rows:
        local = row.occurred_at.astimezone(zone)
        minute_of_day = local.hour * 60 + local.minute
        bucket_minute = minute_of_day - minute_of_day % bucket_minutes
        bucket = local.replace(
            hour=bucket_minute // 60,
            minute=bucket_minute % 60,
            second=0,
            microsecond=0,
        )
        grouped.setdefault((row.metric_type, row.unit, bucket), []).append(row)
    return [
        {
            "metric_type": metric,
            "unit": unit,
            "bucket_start_local": bucket,
            "sample_count": len(samples),
            "minimum": min(sample.value for sample in samples),
            "average": sum((sample.value for sample in samples), Decimal(0))
            / Decimal(len(samples)),
            "maximum": max(sample.value for sample in samples),
        }
        for (metric, unit, bucket), samples in sorted(
            grouped.items(), key=lambda item: (item[0][2], item[0][0].value, item[0][1])
        )
    ]


def _get_wearable_context(
    session: Session, owner_id: uuid.UUID, args: WearableArguments
) -> ChatToolResult:
    metrics = args.metrics or list(GarminMetricType)
    summaries = list(
        session.scalars(
            select(WearableDailySummary)
            .where(
                WearableDailySummary.owner_id == owner_id,
                WearableDailySummary.local_date >= args.date_from,
                WearableDailySummary.local_date <= args.date_to,
                WearableDailySummary.timezone == args.timezone,
                WearableDailySummary.metric_type.in_(metrics),
            )
            .order_by(WearableDailySummary.local_date, WearableDailySummary.metric_type)
        )
    )
    intraday: list[GarminMetricEvent] = []
    if args.include_intraday:
        start, end = _bounds(args)
        intraday = list(
            session.scalars(
                select(GarminMetricEvent)
                .where(
                    GarminMetricEvent.owner_id == owner_id,
                    GarminMetricEvent.aggregation == "provider_sample",
                    GarminMetricEvent.metric_type.in_(metrics),
                    GarminMetricEvent.occurred_at >= start,
                    GarminMetricEvent.occurred_at < end,
                    event_service.current_fact_predicate(GarminMetricEvent, owner_id=owner_id),
                )
                .order_by(GarminMetricEvent.occurred_at, GarminMetricEvent.id)
            )
        )
    buckets = _bucket_intraday(intraday, timezone=args.timezone, bucket_minutes=args.bucket_minutes)
    if len(buckets) > MAX_DENSE_BUCKETS:  # defensive if provider adds a new unit split
        raise ChatToolError("dense_bucket_limit_exceeded")
    expected_summary_keys = {
        (args.date_from + timedelta(days=offset), metric)
        for offset in range((args.date_to - args.date_from).days + 1)
        for metric in metrics
    }
    actual_summary_keys = {(row.local_date, row.metric_type) for row in summaries}
    return _result(
        name="get_wearable_context",
        data={
            "daily_summaries": [
                {
                    "id": row.id,
                    "local_date": row.local_date,
                    "metric_type": row.metric_type,
                    "unit": row.unit,
                    "sample_count": row.sample_count,
                    "samples_without_cadence": row.samples_without_cadence,
                    "observed_coverage_minutes": row.observed_coverage_minutes,
                    "observed_coverage_percent": row.observed_coverage_percent,
                    "missingness_state": row.missingness_state,
                    "minimum": row.minimum,
                    "average": row.average,
                    "maximum": row.maximum,
                    "source_revision_watermark_sha256": row.source_revision_watermark_sha256,
                    "summary_version": row.summary_version,
                }
                for row in summaries
            ],
            "intraday_buckets": buckets,
            "intraday_bucket_minutes": args.bucket_minutes if args.include_intraday else None,
        },
        missingness={
            "missing_daily_summary_count": len(expected_summary_keys - actual_summary_keys),
            "intraday_requested": args.include_intraday,
            "no_intraday_samples": args.include_intraday and not intraday,
            "missing_is_never_zero": True,
        },
        source_manifest={
            "wearable_summary": [str(row.id) for row in summaries],
            "fact": [str(row.id) for row in intraday],
        },
        timezone=args.timezone,
        date_scope=_date_scope(args.date_from, args.date_to),
    )


def _nearest_sample(
    samples: list[dict[str, object]], *, anchor_at: datetime
) -> dict[str, object] | None:
    if not samples:
        return None
    anchor = anchor_at.astimezone(UTC)
    return min(
        samples,
        key=lambda sample: abs(
            (cast(datetime, sample["occurred_at"]).astimezone(UTC) - anchor).total_seconds()
        ),
    )


def _modeled_curve_position(
    session: Session,
    *,
    owner_id: uuid.UUID,
    anchor_at: datetime,
    timezone: str,
) -> tuple[dict[str, object], list[str]]:
    zone = ZoneInfo(timezone)
    day = anchor_at.astimezone(zone).date()
    curve = injectable_pharmacokinetics.curve_for_owner(
        session,
        owner_id=owner_id,
        day=day,
        timezone=timezone,
    )
    curve_samples = cast(list[dict[str, object]], curve["samples"])
    sample = _nearest_sample(curve_samples, anchor_at=anchor_at)
    instants = [cast(datetime, item["occurred_at"]) for item in curve_samples]
    reference = wake_reference_inputs.reference_from_observed_facts_for_owner(
        session,
        owner_id=owner_id,
        day=day,
        timezone=timezone,
        sample_instants=instants,
    )
    reference_sample = _nearest_sample(
        cast(list[dict[str, object]], reference.get("samples", [])),
        anchor_at=anchor_at,
    )
    position: str | None = None
    if sample is not None and reference_sample is not None:
        modeled = cast(Decimal, sample["modeled_free_cortisol_nmol_l"])
        p5 = cast(Decimal, reference_sample["serum_free_p5_nmol_l"])
        p50 = cast(Decimal, reference_sample["serum_free_p50_nmol_l"])
        p95 = cast(Decimal, reference_sample["serum_free_p95_nmol_l"])
        if modeled < p5:
            position = "below_recorded_reference_p5"
        elif modeled < p50:
            position = "between_recorded_reference_p5_and_p50"
        elif modeled <= p95:
            position = "between_recorded_reference_p50_and_p95"
        else:
            position = "above_recorded_reference_p95"
    marker_ids = [
        str(marker["dose_event_id"])
        for marker in cast(list[dict[str, object]], curve["dose_markers"])
    ]
    return (
        {
            "model_id": injectable_pharmacokinetics.MODEL_ID,
            "model_revision": injectable_pharmacokinetics.MODEL_REVISION,
            "series_name": curve["series_name"],
            "unit": curve["series_unit"],
            "sample_at": None if sample is None else sample["occurred_at"],
            "modeled_free_cortisol_nmol_l": (
                None if sample is None else sample["modeled_free_cortisol_nmol_l"]
            ),
            "regular_modeled_free_cortisol_nmol_l": (
                None if sample is None else sample["regular_modeled_free_cortisol_nmol_l"]
            ),
            "stress_modeled_free_cortisol_nmol_l": (
                None if sample is None else sample["stress_modeled_free_cortisol_nmol_l"]
            ),
            "reference_p5_nmol_l": (
                None if reference_sample is None else reference_sample["serum_free_p5_nmol_l"]
            ),
            "reference_p50_nmol_l": (
                None if reference_sample is None else reference_sample["serum_free_p50_nmol_l"]
            ),
            "reference_p95_nmol_l": (
                None if reference_sample is None else reference_sample["serum_free_p95_nmol_l"]
            ),
            "reference_position": position,
            "reference_available": reference_sample is not None,
            "safety_boundary": (
                "Modeled population-parameter context, not a cortisol measurement, personal "
                "target, medication-adequacy test, alert, or dosing guide."
            ),
        },
        marker_ids,
    )


def _sleep_before(
    session: Session,
    *,
    owner_id: uuid.UUID,
    anchor_at: datetime,
    history_start: datetime,
) -> tuple[dict[str, object] | None, list[str]]:
    anchor = anchor_at.astimezone(UTC)
    rows = list(
        session.scalars(
            select(GarminSleepEvent)
            .where(
                GarminSleepEvent.owner_id == owner_id,
                GarminSleepEvent.ended_at <= anchor,
                GarminSleepEvent.ended_at >= history_start.astimezone(UTC),
                event_service.current_fact_predicate(GarminSleepEvent, owner_id=owner_id),
            )
            .order_by(GarminSleepEvent.ended_at.desc(), GarminSleepEvent.id)
            .limit(31)
        )
    )
    if not rows:
        return None, []
    latest = rows[0]
    baseline = rows[1:]
    duration_hours = (
        None
        if latest.duration_seconds is None
        else Decimal(latest.duration_seconds) / Decimal(3_600)
    )
    baseline_durations = [
        Decimal(row.duration_seconds) / Decimal(3_600)
        for row in baseline
        if row.duration_seconds is not None
    ]
    baseline_scores = [
        Decimal(row.overall_sleep_score) for row in baseline if row.overall_sleep_score is not None
    ]
    average_duration = (
        sum(baseline_durations, Decimal(0)) / Decimal(len(baseline_durations))
        if baseline_durations
        else None
    )
    average_score = (
        sum(baseline_scores, Decimal(0)) / Decimal(len(baseline_scores))
        if baseline_scores
        else None
    )
    return (
        {
            "started_at": latest.occurred_at,
            "ended_at": latest.ended_at,
            "duration_hours": duration_hours,
            "overall_sleep_score": latest.overall_sleep_score,
            "awakenings": latest.awakenings,
            "baseline_session_count": len(baseline),
            "baseline_average_duration_hours": average_duration,
            "duration_difference_from_baseline_hours": (
                None
                if average_duration is None or duration_hours is None
                else duration_hours - average_duration
            ),
            "baseline_average_sleep_score": average_score,
            "sleep_score_difference_from_baseline": (
                None
                if average_score is None or latest.overall_sleep_score is None
                else Decimal(latest.overall_sleep_score) - average_score
            ),
            "comparison_is_descriptive_not_a_quality_diagnosis": True,
        },
        [str(row.id) for row in rows],
    )


def _weather_before(
    session: Session,
    *,
    owner_id: uuid.UUID,
    anchor_at: datetime,
    earliest_at: datetime,
) -> tuple[dict[str, object] | None, list[str]]:
    row = session.scalar(
        select(ContextEvent)
        .where(
            ContextEvent.owner_id == owner_id,
            ContextEvent.occurred_at <= anchor_at.astimezone(UTC),
            ContextEvent.occurred_at >= earliest_at.astimezone(UTC),
            ContextEvent.weather_provider.is_not(None),
            event_service.current_fact_predicate(ContextEvent, owner_id=owner_id),
        )
        .order_by(ContextEvent.weather_observed_at.desc().nullslast(), ContextEvent.id)
        .limit(1)
    )
    if row is None:
        return None, []
    return (
        {
            "observed_at": row.weather_observed_at,
            "temperature": row.temperature,
            "apparent_temperature": row.apparent_temperature,
            "temperature_unit": row.temperature_unit,
            "humidity_percent": row.humidity_percent,
            "precipitation": row.precipitation,
            "precipitation_unit": row.precipitation_unit,
            "conditions": row.conditions,
            "wind_speed_kph": row.wind_speed_kph,
            "wind_gust_kph": row.wind_gust_kph,
            "location": row.coarse_location_label,
            "provider": row.weather_provider,
        },
        [str(row.id)],
    )


def _wearable_window_comparison(
    session: Session,
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
    timezone: str,
    history_days: int,
) -> tuple[list[dict[str, object]], list[str]]:
    rows = list(
        session.scalars(
            select(GarminMetricEvent)
            .where(
                GarminMetricEvent.owner_id == owner_id,
                GarminMetricEvent.aggregation == "provider_sample",
                GarminMetricEvent.occurred_at >= start.astimezone(UTC),
                GarminMetricEvent.occurred_at <= end.astimezone(UTC),
                event_service.current_fact_predicate(GarminMetricEvent, owner_id=owner_id),
            )
            .order_by(GarminMetricEvent.occurred_at, GarminMetricEvent.id)
        )
    )
    grouped: dict[tuple[GarminMetricType, str | None], list[GarminMetricEvent]] = {}
    for row in rows:
        grouped.setdefault((row.metric_type, row.unit), []).append(row)
    anchor_day = end.astimezone(ZoneInfo(timezone)).date()
    baseline_rows = list(
        session.scalars(
            select(WearableDailySummary)
            .where(
                WearableDailySummary.owner_id == owner_id,
                WearableDailySummary.local_date >= anchor_day - timedelta(days=history_days),
                WearableDailySummary.local_date < anchor_day,
                WearableDailySummary.timezone == timezone,
            )
            .order_by(WearableDailySummary.local_date, WearableDailySummary.metric_type)
        )
    )
    baselines: dict[tuple[GarminMetricType, str | None], list[WearableDailySummary]] = {}
    for row in baseline_rows:
        baselines.setdefault((row.metric_type, row.unit), []).append(row)
    comparisons: list[dict[str, object]] = []
    for key, samples in sorted(
        grouped.items(), key=lambda item: (item[0][0].value, item[0][1] or "")
    ):
        values = [sample.value for sample in samples]
        window_average = sum(values, Decimal(0)) / Decimal(len(values))
        baseline = baselines.get(key, [])
        baseline_averages = [row.average for row in baseline if row.average is not None]
        baseline_minimum = min(baseline_averages) if baseline_averages else None
        baseline_maximum = max(baseline_averages) if baseline_averages else None
        comparison = "baseline_unavailable"
        if baseline_minimum is not None and baseline_maximum is not None:
            comparison = (
                "outside_recorded_daily_average_range"
                if window_average < baseline_minimum or window_average > baseline_maximum
                else "within_recorded_daily_average_range"
            )
        comparisons.append(
            {
                "metric_type": key[0],
                "unit": key[1],
                "window_sample_count": len(samples),
                "window_minimum": min(values),
                "window_average": window_average,
                "window_maximum": max(values),
                "baseline_day_count": len(baseline_averages),
                "baseline_daily_average_minimum": baseline_minimum,
                "baseline_daily_average_maximum": baseline_maximum,
                "descriptive_comparison": comparison,
            }
        )
    return comparisons, [str(row.id) for row in [*rows, *baseline_rows]]


def _overlapping_stress_episodes(
    session: Session,
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[StressEpisode]:
    return list(
        session.scalars(
            select(StressEpisode)
            .where(
                StressEpisode.owner_id == owner_id,
                StressEpisode.started_at <= end.astimezone(UTC),
                or_(
                    StressEpisode.ended_at.is_(None),
                    StressEpisode.ended_at >= start.astimezone(UTC),
                ),
            )
            .order_by(StressEpisode.started_at, StressEpisode.id)
        )
    )


def _stress_episode_payload(row: StressEpisode) -> dict[str, object]:
    return {
        "id": row.id,
        "trigger": row.trigger,
        "status": row.status,
        "severity": row.severity,
        "started_at": row.started_at,
        "ended_at": row.ended_at,
        "highest_temperature_c": row.highest_temperature_c,
        "outcome": row.outcome,
    }


def _cross_event_patterns(contexts: list[dict[str, object]]) -> dict[str, object]:
    curve_positions: dict[str, int] = {}
    event_types: dict[str, int] = {}
    wearable_outside: dict[str, int] = {}
    sleep_below_baseline = 0
    sleep_comparable = 0
    weather_available = 0
    episode_overlap = 0
    anchor_types: dict[str, int] = {}
    for context in contexts:
        anchor_type = str(context.get("anchor_type") or "symptom")
        anchor_types[anchor_type] = anchor_types.get(anchor_type, 0) + 1
        curve = cast(
            dict[str, object],
            context.get("modeled_curve_at_anchor") or context.get("modeled_curve_at_symptom") or {},
        )
        position = curve.get("reference_position")
        if isinstance(position, str):
            curve_positions[position] = curve_positions.get(position, 0) + 1
        sleep = context.get("sleep_before_anchor") or context.get("sleep_before_symptom")
        if isinstance(sleep, dict):
            difference = sleep.get("duration_difference_from_baseline_hours")
            if isinstance(difference, Decimal):
                sleep_comparable += 1
                if difference < 0:
                    sleep_below_baseline += 1
        if (
            context.get("weather_before_anchor") is not None
            or context.get("weather_before_symptom") is not None
        ):
            weather_available += 1
        episodes = context.get("overlapping_stress_episodes")
        if isinstance(episodes, list) and episodes:
            episode_overlap += 1
        events = context.get("preceding_recorded_events")
        if isinstance(events, list):
            for event in events:
                if not isinstance(event, dict):
                    continue
                record_type = event.get("record_type")
                if isinstance(record_type, str):
                    event_types[record_type] = event_types.get(record_type, 0) + 1
        comparisons = context.get("wearable_window_comparisons")
        if isinstance(comparisons, list):
            for comparison in comparisons:
                if not isinstance(comparison, dict):
                    continue
                if (
                    comparison.get("descriptive_comparison")
                    != "outside_recorded_daily_average_range"
                ):
                    continue
                metric = comparison.get("metric_type")
                metric_name = metric.value if isinstance(metric, GarminMetricType) else str(metric)
                wearable_outside[metric_name] = wearable_outside.get(metric_name, 0) + 1
    return {
        "prior_event_count": len(contexts),
        "anchor_type_counts": anchor_types,
        "curve_reference_position_counts": curve_positions,
        "events_in_preceding_windows_by_type": event_types,
        "wearable_outside_recorded_range_counts": wearable_outside,
        "sleep_below_own_baseline_count": sleep_below_baseline,
        "sleep_comparable_event_count": sleep_comparable,
        "weather_available_event_count": weather_available,
        "stress_episode_overlap_count": episode_overlap,
        "method": (
            "Each prior symptom or selected stress episode is an event anchor. The same "
            "preceding-hours window is applied around every anchor, then deterministic "
            "category counts are compared."
        ),
        "caution": (
            "Repeated temporal context is an association for review; it does not establish "
            "cause, diagnosis, or medication need."
        ),
    }


def _get_preceding_health_context(
    session: Session,
    owner_id: uuid.UUID,
    args: PrecedingHealthContextArguments,
) -> ChatToolResult:
    anchor = args.anchor_at.astimezone(UTC)
    start = anchor - timedelta(hours=args.lookback_hours)
    history_start = anchor - timedelta(days=args.history_days)
    event_models: tuple[type[EventMixin], ...] = (
        DoseEvent,
        SymptomEvent,
        MealEvent,
        BloodPressureEvent,
        TemperatureEvent,
        WeightEvent,
        GarminActivityEvent,
        EmergencyInjectionEvent,
    )
    event_rows = [
        row
        for model in event_models
        for row in _current_rows(
            session,
            model,
            owner_id=owner_id,
            start=start,
            end=anchor + timedelta(microseconds=1),
            limit=50,
        )
    ]
    event_rows.sort(key=lambda row: (row.occurred_at, row.id))
    event_rows = event_rows[-50:]
    current_episodes = _overlapping_stress_episodes(
        session,
        owner_id=owner_id,
        start=start,
        end=anchor,
    )

    curve, curve_ids = _modeled_curve_position(
        session,
        owner_id=owner_id,
        anchor_at=anchor,
        timezone=args.timezone,
    )
    sleep, sleep_ids = _sleep_before(
        session,
        owner_id=owner_id,
        anchor_at=anchor,
        history_start=history_start,
    )
    weather, weather_ids = _weather_before(
        session,
        owner_id=owner_id,
        anchor_at=anchor,
        earliest_at=start - timedelta(hours=6),
    )
    wearable, wearable_ids = _wearable_window_comparison(
        session,
        owner_id=owner_id,
        start=start,
        end=anchor,
        timezone=args.timezone,
        history_days=min(args.history_days, 30),
    )

    current_symptoms = [row for row in event_rows if isinstance(row, SymptomEvent)]
    inferred_symptom_name = current_symptoms[-1].name if current_symptoms else None
    symptom_filter = args.symptom_name or inferred_symptom_name
    historical_end = (
        start if inferred_symptom_name is not None and args.symptom_name is None else anchor
    )
    symptom_statement = select(SymptomEvent).where(
        SymptomEvent.owner_id == owner_id,
        SymptomEvent.occurred_at < historical_end,
        SymptomEvent.occurred_at >= history_start,
        event_service.current_fact_predicate(SymptomEvent, owner_id=owner_id),
    )
    if symptom_filter is not None:
        symptom_statement = symptom_statement.where(
            func.lower(SymptomEvent.name) == symptom_filter.casefold()
        )
    symptom_rows = list(
        session.scalars(
            symptom_statement.order_by(SymptomEvent.occurred_at.desc(), SymptomEvent.id).limit(
                args.similar_limit
            )
        )
    )
    episode_rows: list[StressEpisode] = []
    if args.include_stress_episode_anchors:
        episode_rows = list(
            session.scalars(
                select(StressEpisode)
                .where(
                    StressEpisode.owner_id == owner_id,
                    StressEpisode.started_at < start,
                    StressEpisode.started_at >= history_start,
                )
                .order_by(StressEpisode.started_at.desc(), StressEpisode.id)
                .limit(args.similar_limit)
            )
        )
    anchors: list[tuple[datetime, str, SymptomEvent | StressEpisode]] = [
        (row.occurred_at, "symptom", row) for row in symptom_rows
    ]
    anchors.extend((row.started_at, "stress_episode", row) for row in episode_rows)
    anchors.sort(key=lambda item: (item[0], item[2].id), reverse=True)
    anchors = anchors[: args.similar_limit]
    similar: list[dict[str, object]] = []
    similar_ids: list[str] = []
    for anchor_at, anchor_type, anchor_row in anchors:
        symptom = anchor_row if isinstance(anchor_row, SymptomEvent) else None
        episode = anchor_row if isinstance(anchor_row, StressEpisode) else None
        anchor_start = anchor_at - timedelta(hours=args.lookback_hours)
        symptom_curve, symptom_curve_ids = _modeled_curve_position(
            session,
            owner_id=owner_id,
            anchor_at=anchor_at,
            timezone=args.timezone,
        )
        symptom_weather, symptom_weather_ids = _weather_before(
            session,
            owner_id=owner_id,
            anchor_at=anchor_at,
            earliest_at=anchor_at - timedelta(hours=12),
        )
        symptom_sleep, symptom_sleep_ids = _sleep_before(
            session,
            owner_id=owner_id,
            anchor_at=anchor_at,
            history_start=anchor_at - timedelta(days=30),
        )
        symptom_events = [
            row
            for model in event_models
            for row in _current_rows(
                session,
                model,
                owner_id=owner_id,
                start=anchor_start,
                end=anchor_at + timedelta(microseconds=1),
                limit=30,
            )
            if symptom is None or row.id != symptom.id
        ]
        symptom_events.sort(key=lambda row: (row.occurred_at, row.id))
        symptom_episodes = _overlapping_stress_episodes(
            session,
            owner_id=owner_id,
            start=anchor_start,
            end=anchor_at,
        )
        symptom_wearable, symptom_wearable_ids = _wearable_window_comparison(
            session,
            owner_id=owner_id,
            start=anchor_start,
            end=anchor_at,
            timezone=args.timezone,
            history_days=30,
        )
        similar.append(
            {
                "anchor_type": anchor_type,
                "symptom": (
                    _event_payload(symptom, include_sensitive=False)
                    if symptom is not None
                    else None
                ),
                "stress_episode": (
                    _stress_episode_payload(episode) if episode is not None else None
                ),
                "window_started_at": anchor_start,
                "preceding_recorded_events": [
                    _event_payload(row, include_sensitive=False) for row in symptom_events[-30:]
                ],
                "overlapping_stress_episodes": [
                    _stress_episode_payload(row) for row in symptom_episodes
                ],
                "modeled_curve_at_symptom": symptom_curve,
                "modeled_curve_at_anchor": symptom_curve,
                "weather_before_symptom": symptom_weather,
                "weather_before_anchor": symptom_weather,
                "sleep_before_symptom": symptom_sleep,
                "sleep_before_anchor": symptom_sleep,
                "wearable_window_comparisons": symptom_wearable,
            }
        )
        similar_ids.extend(
            [
                str(anchor_row.id),
                *(str(row.id) for row in symptom_events),
                *(str(row.id) for row in symptom_episodes),
                *symptom_curve_ids,
                *symptom_weather_ids,
                *symptom_sleep_ids,
                *symptom_wearable_ids,
            ]
        )

    source_ids = sorted(
        {
            *(str(row.id) for row in event_rows),
            *(str(row.id) for row in current_episodes),
            *curve_ids,
            *sleep_ids,
            *weather_ids,
            *wearable_ids,
            *similar_ids,
        }
    )
    local_anchor = anchor.astimezone(ZoneInfo(args.timezone))
    return _result(
        name="get_preceding_health_context",
        data={
            "anchor_at": local_anchor,
            "window_started_at": start.astimezone(ZoneInfo(args.timezone)),
            "lookback_hours": args.lookback_hours,
            "recorded_events": [_event_payload(row, include_sensitive=False) for row in event_rows],
            "overlapping_stress_episodes": [
                _stress_episode_payload(row) for row in current_episodes
            ],
            "modeled_curve_at_anchor": curve,
            "weather_before_anchor": weather,
            "sleep_before_anchor": sleep,
            "wearable_window_comparisons": wearable,
            "prior_symptom_contexts": [
                context for context in similar if context["anchor_type"] == "symptom"
            ],
            "prior_event_contexts": similar,
            "similar_symptom_filter": symptom_filter,
            "cross_event_patterns": _cross_event_patterns(similar),
            "interpretation_boundary": (
                "All comparisons are descriptive context. They do not establish cause, "
                "diagnosis, medication adequacy, or dosing guidance."
            ),
        },
        missingness={
            "no_recorded_events_in_window": not event_rows,
            "weather_not_recorded": weather is None,
            "sleep_not_recorded": sleep is None,
            "no_wearable_samples_in_window": not wearable,
            "no_prior_event_anchors_in_history": not similar,
            "missing_is_never_zero": True,
        },
        source_manifest={"fact_and_deterministic_projection": source_ids},
        timezone=args.timezone,
        date_scope=_date_scope(
            history_start.astimezone(ZoneInfo(args.timezone)).date(), local_anchor.date()
        ),
    )


def _get_lab_trends(
    session: Session, owner_id: uuid.UUID, args: LabTrendArguments
) -> ChatToolResult:
    start, end = _bounds(args)
    statement = (
        select(LabResult, LabPanel)
        .join(LabPanel, LabPanel.id == LabResult.panel_id)
        .where(
            LabResult.owner_id == owner_id,
            LabPanel.owner_id == owner_id,
            LabPanel.occurred_at >= start,
            LabPanel.occurred_at < end,
            event_service.current_fact_predicate(LabPanel, owner_id=owner_id),
        )
    )
    if args.analytes:
        lowered = [name.casefold() for name in args.analytes]
        statement = statement.where(func.lower(LabResult.analyte_name).in_(lowered))
    rows = session.execute(
        statement.order_by(
            LabPanel.occurred_at.desc(), LabResult.source_row_index, LabResult.id
        ).limit(args.limit)
    ).all()
    return _result(
        name="get_lab_trends",
        data={
            "results": [
                {
                    "id": result.id,
                    "panel_id": panel.id,
                    "specimen_time": panel.occurred_at,
                    "specimen_timezone": panel.timezone,
                    "laboratory_name": panel.laboratory_name,
                    "specimen_type": panel.specimen_type,
                    "analyte_name": result.analyte_name,
                    "original_value": result.original_value,
                    "qualitative_result": result.qualitative_result,
                    "original_unit": result.original_unit,
                    "original_reference_range": result.original_reference_range,
                    "abnormal_flag": result.abnormal_flag,
                    "normalized_analyte_code": result.normalized_analyte_code,
                    "normalized_value": result.normalized_value,
                    "normalized_unit": result.normalized_unit,
                    "normalization_method": result.normalization_method,
                }
                for result, panel in rows
            ]
        },
        missingness={"no_matching_lab_results": not rows},
        source_manifest={
            "fact": sorted(
                {str(item_id) for result, panel in rows for item_id in (result.id, panel.id)}
            )
        },
        timezone=args.timezone,
        date_scope=_date_scope(args.date_from, args.date_to),
    )


def _get_data_availability(
    session: Session, owner_id: uuid.UUID, args: DataAvailabilityArguments
) -> ChatToolResult:
    start, end = _bounds(args)
    event_models: list[tuple[str, type[EventMixin]]] = [
        ("doses", DoseEvent),
        ("symptoms", SymptomEvent),
        ("meals", MealEvent),
        ("diary", DiaryEvent),
        ("life_events", LifeEvent),
        ("blood_pressure", BloodPressureEvent),
        ("weight", WeightEvent),
        ("temperature", TemperatureEvent),
        ("garmin_metrics", GarminMetricEvent),
        ("garmin_sleep", GarminSleepEvent),
        ("garmin_activities", GarminActivityEvent),
        ("emergency_injections", EmergencyInjectionEvent),
        ("lab_panels", LabPanel),
    ]
    counts = {
        name: session.scalar(
            select(func.count())
            .select_from(model)
            .where(
                model.owner_id == owner_id,
                model.occurred_at >= start,
                model.occurred_at < end,
                event_service.current_fact_predicate(model, owner_id=owner_id),
            )
        )
        or 0
        for name, model in event_models
    }
    episode_count = (
        session.scalar(
            select(func.count())
            .select_from(StressEpisode)
            .where(
                StressEpisode.owner_id == owner_id,
                StressEpisode.started_at < end,
                or_(StressEpisode.ended_at.is_(None), StressEpisode.ended_at > start),
            )
        )
        or 0
    )
    counts["stress_episodes"] = episode_count
    plan_count = (
        session.scalar(
            select(func.count())
            .select_from(RegimenVersion)
            .where(
                RegimenVersion.owner_id == owner_id,
                RegimenVersion.status == RegimenStatus.APPROVED,
            )
        )
        or 0
    )
    counts["physician_approved_plans"] = plan_count
    return _result(
        name="get_data_availability",
        data={"counts": counts},
        missingness={
            "missing_domains": sorted(name for name, count in counts.items() if not count)
        },
        source_manifest={"scope": [f"owner-range:{_canonical_sha([owner_id, start, end])}"]},
        timezone=args.timezone,
        date_scope=_date_scope(args.date_from, args.date_to),
    )


def _compare_periods(
    session: Session, owner_id: uuid.UUID, args: ComparePeriodsArguments
) -> ChatToolResult:
    first = analytics_service.summary_for_owner(
        session,
        owner_id=owner_id,
        date_from=args.first.date_from,
        date_to=args.first.date_to,
        timezone=args.timezone,
    )
    second = analytics_service.summary_for_owner(
        session,
        owner_id=owner_id,
        date_from=args.second.date_from,
        date_to=args.second.date_to,
        timezone=args.timezone,
    )
    source_scope = {
        "first": _date_scope(args.first.date_from, args.first.date_to),
        "second": _date_scope(args.second.date_from, args.second.date_to),
    }
    first_daily_doses = cast(dict[str, Any], first["daily_doses"])
    second_daily_doses = cast(dict[str, Any], second["daily_doses"])
    return _result(
        name="compare_periods",
        data={
            "first": first,
            "second": second,
            "comparison_method": (
                "Descriptive deterministic period summaries are shown side by side; "
                "no causal or diagnostic inference is made."
            ),
        },
        missingness={
            "first_daily_dose_missing_count": first_daily_doses["missing_count"],
            "second_daily_dose_missing_count": second_daily_doses["missing_count"],
        },
        source_manifest={"scope": [f"periods:{_canonical_sha(source_scope)}"]},
        timezone=args.timezone,
        date_scope={
            "first_from": args.first.date_from.isoformat(),
            "first_to": args.first.date_to.isoformat(),
            "second_from": args.second.date_from.isoformat(),
            "second_to": args.second.date_to.isoformat(),
        },
    )


def _get_report_snapshot_context(
    session: Session, owner_id: uuid.UUID, args: ReportSnapshotArguments
) -> ChatToolResult:
    snapshot = session.scalar(
        select(ReportSnapshot).where(
            ReportSnapshot.id == args.snapshot_id,
            ReportSnapshot.owner_id == owner_id,
        )
    )
    if snapshot is None:
        raise ChatToolError("report_snapshot_not_found")
    content = dict(snapshot.snapshot_content)
    if not args.include_ai_section:
        content.pop("ai", None)
    source_manifest = {
        key: [str(value) for value in values]
        for key, values in snapshot.source_manifest.items()
        if args.include_ai_section or key != "ai"
    }
    return _result(
        name="get_report_snapshot_context",
        data={
            "snapshot": {
                "id": snapshot.id,
                "date_from": snapshot.date_from,
                "date_to": snapshot.date_to,
                "timezone": snapshot.timezone,
                "selected_sections": snapshot.selected_sections,
                "include_ai_in_original_snapshot": snapshot.include_ai,
                "metric_values": snapshot.metric_values,
                "snapshot_content": content,
                "render_version": snapshot.render_version,
                "canonical_sha256": snapshot.canonical_sha256,
                "created_at": snapshot.created_at,
            }
        },
        missingness={"ai_section_included": args.include_ai_section and "ai" in content},
        source_manifest=source_manifest,
        timezone=snapshot.timezone,
        date_scope=_date_scope(snapshot.date_from, snapshot.date_to),
    )


_HANDLERS: Final[dict[str, Callable[[Session, uuid.UUID, Any], ChatToolResult]]] = {
    "get_data_availability": _get_data_availability,
    "get_daily_healthcurve": _get_daily_healthcurve,
    "search_timeline": _search_timeline,
    "get_medication_context": _get_medication_context,
    "get_symptom_episode_context": _get_symptom_episode_context,
    "get_wearable_context": _get_wearable_context,
    "get_preceding_health_context": _get_preceding_health_context,
    "get_lab_trends": _get_lab_trends,
    "compare_periods": _compare_periods,
    "get_report_snapshot_context": _get_report_snapshot_context,
}


def execute_chat_tool(
    session: Session,
    *,
    owner_id: uuid.UUID,
    tool_name: str,
    arguments: dict[str, object],
    allow_sensitive_text: bool = False,
) -> ChatToolResult:
    """Validate and execute one allow-listed tool under a read-only transaction."""
    argument_model = _ARGUMENT_MODELS.get(tool_name)
    handler = _HANDLERS.get(tool_name)
    if argument_model is None or handler is None:
        raise ChatToolError("unknown_tool")
    try:
        parsed = argument_model.model_validate(arguments)
    except Exception as exc:
        raise ChatToolError("invalid_tool_arguments") from exc
    if isinstance(parsed, (DailyHealthCurveArguments, TimelineArguments)):
        requested = parsed.include_sensitive_text
        if requested and not allow_sensitive_text:
            raise ChatToolError("sensitive_text_not_enabled")
    _read_only(session)
    try:
        return handler(session, owner_id, parsed)
    except ChatToolError:
        raise
    except Exception as exc:
        raise ChatToolError("tool_execution_failed") from exc


def validate_tool_arguments(tool_name: str, arguments: dict[str, object]) -> ToolArguments:
    """Public validator used by the planner before any database operation."""
    model = _ARGUMENT_MODELS.get(tool_name)
    if model is None:
        raise ChatToolError("unknown_tool")
    try:
        return TypeAdapter(model).validate_python(arguments)
    except Exception as exc:
        raise ChatToolError("invalid_tool_arguments") from exc
