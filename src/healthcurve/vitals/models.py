"""Narrowly scoped blood-pressure, body-weight, and temperature facts."""

from __future__ import annotations

from decimal import Decimal
from enum import StrEnum

from sqlalchemy import CheckConstraint, Numeric, SmallInteger
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import FactBase, StrEnumType
from healthcurve.events.base import EventMixin, event_table_args


class WeightUnit(StrEnum):
    KG = "kg"
    LB = "lb"


class TemperatureUnit(StrEnum):
    CELSIUS = "c"
    FAHRENHEIT = "f"


class MeasurementSetting(StrEnum):
    """Where a person says a manual measurement was taken, not how it entered the app."""

    HOME = "home"
    PROVIDER = "provider"


class BodyPosition(StrEnum):
    """Explicit position at a blood-pressure reading; omitted remains unknown."""

    LYING = "lying"
    SITTING = "sitting"
    STANDING = "standing"


class BloodPressureEvent(EventMixin, FactBase):
    """A paired blood-pressure reading, with optional measured pulse."""

    __tablename__ = "blood_pressure_event"

    systolic_mmhg: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    diastolic_mmhg: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    pulse_bpm: Mapped[int | None] = mapped_column(SmallInteger)
    measurement_setting: Mapped[MeasurementSetting] = mapped_column(
        StrEnumType(MeasurementSetting, 16), nullable=False, default=MeasurementSetting.HOME
    )
    body_position: Mapped[BodyPosition | None] = mapped_column(StrEnumType(BodyPosition, 16))

    __table_args__ = (
        CheckConstraint("systolic_mmhg BETWEEN 1 AND 500", name="systolic_structural_range"),
        CheckConstraint("diastolic_mmhg BETWEEN 1 AND 500", name="diastolic_structural_range"),
        CheckConstraint(
            "pulse_bpm IS NULL OR pulse_bpm BETWEEN 1 AND 500", name="pulse_structural_range"
        ),
        CheckConstraint(
            "body_position IS NULL OR body_position IN ('lying', 'sitting', 'standing')",
            name="body_position_supported",
        ),
        *event_table_args("blood_pressure_event"),
    )


class WeightEvent(EventMixin, FactBase):
    """Body weight preserving entered units plus deterministic kilograms."""

    __tablename__ = "weight_event"

    value: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    unit: Mapped[WeightUnit] = mapped_column(StrEnumType(WeightUnit, 8), nullable=False)
    normalized_kg: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    measurement_setting: Mapped[MeasurementSetting] = mapped_column(
        StrEnumType(MeasurementSetting, 16), nullable=False, default=MeasurementSetting.HOME
    )

    __table_args__ = (
        CheckConstraint("value > 0 AND value <= 5000", name="value_structural_range"),
        CheckConstraint(
            "normalized_kg > 0 AND normalized_kg <= 5000",
            name="normalized_kg_structural_range",
        ),
        *event_table_args("weight_event"),
    )


class TemperatureEvent(EventMixin, FactBase):
    """Measured body temperature preserving entered units plus Celsius."""

    __tablename__ = "temperature_event"

    value: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)
    unit: Mapped[TemperatureUnit] = mapped_column(StrEnumType(TemperatureUnit, 8), nullable=False)
    normalized_c: Mapped[Decimal] = mapped_column(Numeric(6, 2), nullable=False)

    __table_args__ = (
        CheckConstraint("normalized_c BETWEEN 25 AND 45", name="human_measurement_range"),
        *event_table_args("temperature_event"),
    )
