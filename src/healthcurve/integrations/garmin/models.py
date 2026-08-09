"""Confirmed Garmin import facts and their exact source provenance."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    LargeBinary,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, declarative_mixin, mapped_column

from healthcurve.db import FACT_SCHEMA, FactBase, StrEnumType
from healthcurve.events.base import EventMixin, event_table_args


class GarminMetricType(StrEnum):
    HEART_RATE = "heart_rate"
    RESTING_HEART_RATE = "resting_heart_rate"
    HRV = "hrv"
    STRESS = "stress"
    BODY_BATTERY = "body_battery"
    STEPS = "steps"
    MODERATE_INTENSITY_MINUTES = "moderate_intensity_minutes"
    VIGOROUS_INTENSITY_MINUTES = "vigorous_intensity_minutes"


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

    garmin_import_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fact.garmin_import_batch.id", ondelete="CASCADE"), nullable=False, index=True
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
    garmin_field_name: Mapped[str] = mapped_column(String(120), nullable=False)

    __table_args__ = (
        CheckConstraint(
            "period_end_at IS NULL OR period_end_at >= occurred_at", name="period_ordered"
        ),
        CheckConstraint("value >= 0", name="metric_nonnegative"),
        *event_table_args("garmin_metric_event"),
    )


class GarminSleepEvent(GarminSourceMixin, EventMixin, FactBase):
    __tablename__ = "garmin_sleep_event"

    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    overall_sleep_score: Mapped[int | None] = mapped_column()
    stage_count: Mapped[int] = mapped_column(nullable=False)

    __table_args__ = (
        CheckConstraint("ended_at > occurred_at", name="sleep_interval_ordered"),
        CheckConstraint(
            "overall_sleep_score IS NULL OR overall_sleep_score BETWEEN 0 AND 100",
            name="sleep_score_range",
        ),
        CheckConstraint("stage_count >= 2", name="sleep_has_explicit_bounds"),
        *event_table_args("garmin_sleep_event"),
    )


class GarminActivityEvent(GarminSourceMixin, EventMixin, FactBase):
    __tablename__ = "garmin_activity_event"

    ended_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    sport: Mapped[str] = mapped_column(String(80), nullable=False)
    sub_sport: Mapped[str | None] = mapped_column(String(80))
    title: Mapped[str | None] = mapped_column(String(300))
    elapsed_seconds: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    distance_m: Mapped[Decimal | None] = mapped_column(Numeric(14, 3))
    calories: Mapped[int | None] = mapped_column()
    average_heart_rate: Mapped[int | None] = mapped_column()
    maximum_heart_rate: Mapped[int | None] = mapped_column()
    source_notes: Mapped[str | None] = mapped_column(Text)

    __table_args__ = (
        CheckConstraint("ended_at >= occurred_at", name="activity_interval_ordered"),
        CheckConstraint(
            "elapsed_seconds IS NULL OR elapsed_seconds >= 0", name="elapsed_nonnegative"
        ),
        CheckConstraint("distance_m IS NULL OR distance_m >= 0", name="distance_nonnegative"),
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
        *event_table_args("garmin_activity_event"),
    )
