"""Canonical context observations stored separately from health events."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import Boolean, CheckConstraint, DateTime, Numeric, String
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import FactBase, StrEnumType
from healthcurve.events.base import EventMixin, event_table_args


class LocationPrecision(StrEnum):
    NONE = "none"
    COARSE = "coarse"
    EXACT = "exact"


class TemperatureUnit(StrEnum):
    CELSIUS = "c"
    FAHRENHEIT = "f"


class PressureUnit(StrEnum):
    HPA = "hpa"
    INHG = "inhg"


class PrecipitationUnit(StrEnum):
    MM = "mm"
    INCH = "in"


class ContextEvent(EventMixin, FactBase):
    """Location/timezone/weather observed at an instant.

    This table never points at a health event. Deleting context therefore cannot
    cascade into the health record, and health-event retention cannot retain an exact
    location through a hidden foreign key.
    """

    __tablename__ = "context_event"

    location_precision: Mapped[LocationPrecision] = mapped_column(
        StrEnumType(LocationPrecision, 16), nullable=False
    )
    coarse_location_label: Mapped[str | None] = mapped_column(String(120))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    exact_location_consent: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    weather_provider: Mapped[str | None] = mapped_column(String(64))
    weather_observation_id: Mapped[str | None] = mapped_column(String(255))
    weather_observed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    temperature: Mapped[Decimal | None] = mapped_column(Numeric(6, 2))
    temperature_unit: Mapped[TemperatureUnit | None] = mapped_column(
        StrEnumType(TemperatureUnit, 8)
    )
    pressure: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    pressure_unit: Mapped[PressureUnit | None] = mapped_column(StrEnumType(PressureUnit, 8))
    humidity_percent: Mapped[Decimal | None] = mapped_column(Numeric(5, 2))
    precipitation: Mapped[Decimal | None] = mapped_column(Numeric(8, 2))
    precipitation_unit: Mapped[PrecipitationUnit | None] = mapped_column(
        StrEnumType(PrecipitationUnit, 8)
    )
    conditions: Mapped[str | None] = mapped_column(String(200))
    weather_confidence: Mapped[Decimal | None] = mapped_column(Numeric(4, 3))

    __table_args__ = (
        CheckConstraint("(latitude IS NULL) = (longitude IS NULL)", name="context_coordinate_pair"),
        CheckConstraint("latitude IS NULL OR latitude BETWEEN -90 AND 90", name="latitude_range"),
        CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180", name="longitude_range"
        ),
        CheckConstraint(
            "(location_precision = 'none' AND coarse_location_label IS NULL "
            "AND latitude IS NULL AND exact_location_consent = false) OR "
            "(location_precision = 'coarse' AND coarse_location_label IS NOT NULL "
            "AND latitude IS NULL AND exact_location_consent = false) OR "
            "(location_precision = 'exact' AND latitude IS NOT NULL "
            "AND exact_location_consent = true)",
            name="location_precision_consent",
        ),
        CheckConstraint(
            "(temperature IS NULL) = (temperature_unit IS NULL)",
            name="temperature_has_unit",
        ),
        CheckConstraint("(pressure IS NULL) = (pressure_unit IS NULL)", name="pressure_has_unit"),
        CheckConstraint(
            "(precipitation IS NULL) = (precipitation_unit IS NULL)",
            name="precipitation_has_unit",
        ),
        CheckConstraint(
            "humidity_percent IS NULL OR humidity_percent BETWEEN 0 AND 100",
            name="humidity_range",
        ),
        CheckConstraint(
            "precipitation IS NULL OR precipitation >= 0", name="precipitation_nonnegative"
        ),
        CheckConstraint(
            "weather_confidence IS NULL OR weather_confidence BETWEEN 0 AND 1",
            name="weather_confidence_range",
        ),
        CheckConstraint(
            "(temperature IS NULL AND pressure IS NULL AND humidity_percent IS NULL "
            "AND precipitation IS NULL AND conditions IS NULL "
            "AND weather_provider IS NULL AND weather_observation_id IS NULL "
            "AND weather_observed_at IS NULL AND weather_confidence IS NULL) OR "
            "(weather_provider IS NOT NULL AND weather_observed_at IS NOT NULL)",
            name="weather_has_provenance",
        ),
        *event_table_args("context_event"),
    )
