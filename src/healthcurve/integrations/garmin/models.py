"""Confirmed Garmin import facts and their exact source provenance."""

from __future__ import annotations

import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declarative_mixin, mapped_column, relationship

from healthcurve.db import FACT_SCHEMA, OPS_SCHEMA, FactBase, OpsBase, StrEnumType
from healthcurve.events.base import EventMixin, event_table_args


class GarminMetricType(StrEnum):
    HEART_RATE = "heart_rate"
    RESTING_HEART_RATE = "resting_heart_rate"
    HRV = "hrv"
    RESPIRATION_RATE = "respiration_rate"
    STRESS = "stress"
    BODY_BATTERY = "body_battery"
    STEPS = "steps"
    MODERATE_INTENSITY_MINUTES = "moderate_intensity_minutes"
    VIGOROUS_INTENSITY_MINUTES = "vigorous_intensity_minutes"


class GarminConnectionState(StrEnum):
    CONNECTED = "connected"
    REAUTHENTICATION_REQUIRED = "reauthentication_required"
    DISCONNECT_PENDING = "disconnect_pending"
    DISCONNECTED = "disconnected"


class GarminSyncStatus(StrEnum):
    RUNNING = "running"
    COMPLETED = "completed"
    COMPLETED_WITH_WARNINGS = "completed_with_warnings"


class GarminSyncOrigin(StrEnum):
    """How the provider read that produced a sync run was requested."""

    LEGACY = "legacy"
    SCHEDULED = "scheduled"
    MANUAL = "manual"
    MANUAL_REFRESH = "manual_refresh"


class GarminSleepStage(StrEnum):
    AWAKE = "awake"


class GarminConnection(OpsBase):
    """Non-secret owner-scoped state for the opt-in automatic integration."""

    __tablename__ = "garmin_connection"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    state: Mapped[GarminConnectionState] = mapped_column(
        StrEnumType(GarminConnectionState, 40), nullable=False
    )
    connected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    disconnected_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_success_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    checkpoint_date: Mapped[date | None] = mapped_column(Date)
    capabilities: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False, default=dict)
    client_version: Mapped[str] = mapped_column(String(32), nullable=False)
    last_error_code: Mapped[str | None] = mapped_column(String(64))

    __table_args__ = (OPS_SCHEMA,)


class GarminSyncRun(OpsBase):
    """Privacy-safe provenance for one bounded provider fetch."""

    __tablename__ = "garmin_sync_run"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    requested_start_date: Mapped[date] = mapped_column(Date, nullable=False)
    requested_end_date: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    origin: Mapped[GarminSyncOrigin] = mapped_column(
        StrEnumType(GarminSyncOrigin, 24),
        nullable=False,
        default=GarminSyncOrigin.LEGACY,
        server_default=GarminSyncOrigin.LEGACY.value,
    )
    status: Mapped[GarminSyncStatus] = mapped_column(
        StrEnumType(GarminSyncStatus, 32), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    counts: Mapped[dict[str, int]] = mapped_column(JSONB, nullable=False, default=dict)
    warning_codes: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    client_version: Mapped[str] = mapped_column(String(32), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "requested_end_date >= requested_start_date", name="garmin_sync_date_ordered"
        ),
        CheckConstraint("finished_at >= started_at", name="garmin_sync_time_ordered"),
        OPS_SCHEMA,
    )


class GarminImportBatch(FactBase):
    """One owner-confirmed source file; preview creates no row."""

    __tablename__ = "garmin_import_batch"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    source_name: Mapped[str] = mapped_column(String(255), nullable=False)
    source_media_type: Mapped[str] = mapped_column(String(100), nullable=False)
    source_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    source_byte_size: Mapped[int] = mapped_column(nullable=False)
    #: Exact confirmed input, retained so a future profile can reproduce the import.
    source_payload: Mapped[bytes] = mapped_column(LargeBinary, nullable=False)
    source_members: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    sdk_profile_version: Mapped[str] = mapped_column(String(32), nullable=False)
    observed_metrics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    missing_metrics: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    device_attributions: Mapped[list[dict[str, Any]]] = mapped_column(
        JSONB, nullable=False, default=list
    )
    confirmed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "source_sha256", name="uq_garmin_batch_owner_checksum"),
        CheckConstraint("source_byte_size > 0", name="garmin_batch_nonempty"),
        CheckConstraint(
            "source_byte_size = octet_length(source_payload)", name="garmin_batch_size_matches"
        ),
        FACT_SCHEMA,
    )


@declarative_mixin
class GarminSourceMixin:
    """Attribution copied onto every fact so it survives detached exports."""

    garmin_import_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fact.garmin_import_batch.id", ondelete="CASCADE"), nullable=True, index=True
    )
    garmin_sync_run_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("ops.garmin_sync_run.id", ondelete="RESTRICT"), nullable=True, index=True
    )
    garmin_source_member: Mapped[str] = mapped_column(String(500), nullable=False)
    garmin_manufacturer: Mapped[str] = mapped_column(String(120), nullable=False)
    garmin_product_name: Mapped[str | None] = mapped_column(String(200))
    #: Stable attribution without retaining the device's raw serial number.
    garmin_device_serial_hash: Mapped[str | None] = mapped_column(String(64))


class GarminMetricEvent(GarminSourceMixin, EventMixin, FactBase):
    __tablename__ = "garmin_metric_event"

    metric_type: Mapped[GarminMetricType] = mapped_column(
        StrEnumType(GarminMetricType, 48), nullable=False
    )
    value: Mapped[Decimal] = mapped_column(Numeric(14, 4), nullable=False)
    unit: Mapped[str] = mapped_column(String(32), nullable=False)
    period_end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    aggregation: Mapped[str] = mapped_column(String(32), nullable=False)
    sample_interval_seconds: Mapped[int | None] = mapped_column()
    garmin_field_name: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "period_end_at IS NULL OR period_end_at >= occurred_at", name="period_ordered"
        ),
        CheckConstraint(
            "aggregation IN ('point', 'interval', 'daily_summary', 'provider_sample')",
            name="metric_aggregation_valid",
        ),
        CheckConstraint(
            "sample_interval_seconds IS NULL OR sample_interval_seconds > 0",
            name="sample_interval_positive",
        ),
        CheckConstraint("value >= 0", name="metric_nonnegative"),
        Index("ix_garmin_metric_owner_type_occurred", "owner_id", "metric_type", "occurred_at"),
        Index(
            "ix_garmin_metric_owner_aggregate_occurred",
            "owner_id",
            text("occurred_at DESC"),
            "id",
            postgresql_where=text("aggregation <> 'provider_sample'"),
        ),
        CheckConstraint(
            "(garmin_import_batch_id IS NOT NULL AND garmin_sync_run_id IS NULL) OR "
            "(garmin_import_batch_id IS NULL AND garmin_sync_run_id IS NOT NULL)",
            name="garmin_metric_exactly_one_source",
        ),
        *event_table_args("garmin_metric_event"),
    )


class GarminSleepEvent(GarminSourceMixin, EventMixin, FactBase):
    __tablename__ = "garmin_sleep_event"

    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overall_sleep_score: Mapped[int | None] = mapped_column()
    stage_count: Mapped[int] = mapped_column(nullable=False)
    duration_seconds: Mapped[int | None] = mapped_column()
    garmin_duration_source: Mapped[str] = mapped_column(String(32), nullable=False)
    awakenings: Mapped[int | None] = mapped_column()
    stage_intervals: Mapped[list[GarminSleepStageInterval]] = relationship(
        back_populates="sleep_event",
        cascade="all, delete-orphan",
        order_by="GarminSleepStageInterval.ordinal",
    )

    __table_args__ = (
        CheckConstraint("ended_at > occurred_at", name="sleep_interval_ordered"),
        CheckConstraint(
            "overall_sleep_score IS NULL OR overall_sleep_score BETWEEN 0 AND 100",
            name="sleep_score_range",
        ),
        CheckConstraint("stage_count >= 0", name="sleep_stage_count_nonnegative"),
        CheckConstraint(
            "duration_seconds IS NULL OR duration_seconds >= 0", name="sleep_duration_nonnegative"
        ),
        CheckConstraint(
            "garmin_duration_source IN ('provider', 'calculated_from_bounds')",
            name="sleep_duration_source_valid",
        ),
        CheckConstraint("awakenings IS NULL OR awakenings >= 0", name="awakenings_nonnegative"),
        CheckConstraint(
            "(garmin_import_batch_id IS NOT NULL AND garmin_sync_run_id IS NULL) OR "
            "(garmin_import_batch_id IS NULL AND garmin_sync_run_id IS NOT NULL)",
            name="garmin_sleep_exactly_one_source",
        ),
        *event_table_args("garmin_sleep_event"),
    )


class GarminSleepStageInterval(FactBase):
    """One explicit provider/FIT sleep-stage interval for an immutable sleep revision."""

    __tablename__ = "garmin_sleep_stage_interval"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    sleep_event_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fact.garmin_sleep_event.id", ondelete="CASCADE"), nullable=False, index=True
    )
    ordinal: Mapped[int] = mapped_column(nullable=False)
    stage: Mapped[GarminSleepStage] = mapped_column(
        StrEnumType(GarminSleepStage, 24), nullable=False
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sleep_event: Mapped[GarminSleepEvent] = relationship(back_populates="stage_intervals")

    __table_args__ = (
        CheckConstraint("ordinal >= 0", name="sleep_stage_ordinal_nonnegative"),
        CheckConstraint("ended_at > started_at", name="sleep_stage_interval_ordered"),
        UniqueConstraint("sleep_event_id", "ordinal", name="uq_sleep_stage_interval_ordinal"),
        FACT_SCHEMA,
    )


class GarminActivityEvent(GarminSourceMixin, EventMixin, FactBase):
    __tablename__ = "garmin_activity_event"

    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sport: Mapped[str] = mapped_column(String(80), nullable=False)
    sub_sport: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str | None] = mapped_column(String(300))
    elapsed_seconds: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    distance_miles: Mapped[Decimal | None] = mapped_column(Numeric(14, 4))
    calories: Mapped[int | None] = mapped_column()
    average_heart_rate: Mapped[int | None] = mapped_column()
    maximum_heart_rate: Mapped[int | None] = mapped_column()
    source_notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("ended_at >= occurred_at", name="activity_interval_ordered"),
        CheckConstraint(
            "elapsed_seconds IS NULL OR elapsed_seconds >= 0", name="elapsed_nonnegative"
        ),
        CheckConstraint(
            "distance_miles IS NULL OR distance_miles >= 0", name="distance_nonnegative"
        ),
        CheckConstraint("calories IS NULL OR calories >= 0", name="calories_nonnegative"),
        CheckConstraint(
            "average_heart_rate IS NULL OR average_heart_rate BETWEEN 20 AND 260",
            name="average_hr_plausible",
        ),
        CheckConstraint(
            "maximum_heart_rate IS NULL OR maximum_heart_rate BETWEEN 20 AND 260",
            name="maximum_hr_plausible",
        ),
        Index("ix_garmin_activity_sport_time", "sport", "occurred_at"),
        CheckConstraint(
            "(garmin_import_batch_id IS NOT NULL AND garmin_sync_run_id IS NULL) OR "
            "(garmin_import_batch_id IS NULL AND garmin_sync_run_id IS NOT NULL)",
            name="garmin_activity_exactly_one_source",
        ),
        *event_table_args("garmin_activity_event"),
    )
