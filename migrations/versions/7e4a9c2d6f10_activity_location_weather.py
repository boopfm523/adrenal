"""private Garmin activity location and historical weather context

Revision ID: 7e4a9c2d6f10
Revises: 6d2f8a4c1b90
Create Date: 2026-08-28
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "7e4a9c2d6f10"
down_revision: Union[str, Sequence[str], None] = "6d2f8a4c1b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "garmin_activity_event",
        sa.Column("environment", sa.String(length=16), server_default="unknown", nullable=False),
        schema="fact",
    )
    op.add_column(
        "garmin_activity_event",
        sa.Column("location_name", sa.String(length=120), nullable=True),
        schema="fact",
    )
    op.add_column(
        "garmin_activity_event",
        sa.Column("location_latitude", sa.Numeric(precision=4, scale=1), nullable=True),
        schema="fact",
    )
    op.add_column(
        "garmin_activity_event",
        sa.Column("location_longitude", sa.Numeric(precision=4, scale=1), nullable=True),
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_activity_event_activity_environment_valid"),
        "garmin_activity_event",
        "environment IN ('indoor', 'outdoor', 'unknown')",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_activity_event_activity_location_coordinate_pair"),
        "garmin_activity_event",
        "(location_latitude IS NULL) = (location_longitude IS NULL)",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_activity_event_activity_location_latitude_range"),
        "garmin_activity_event",
        "location_latitude IS NULL OR location_latitude BETWEEN -90 AND 90",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_activity_event_activity_location_longitude_range"),
        "garmin_activity_event",
        "location_longitude IS NULL OR location_longitude BETWEEN -180 AND 180",
        schema="fact",
    )

    op.drop_constraint(
        op.f("ck_context_event_weather_has_provenance"),
        "context_event",
        schema="fact",
        type_="check",
    )
    op.add_column(
        "context_event",
        sa.Column("weather_interval_ended_at", sa.DateTime(timezone=True), nullable=True),
        schema="fact",
    )
    op.add_column(
        "context_event",
        sa.Column("apparent_temperature", sa.Numeric(precision=6, scale=2), nullable=True),
        schema="fact",
    )
    op.add_column(
        "context_event",
        sa.Column("wind_speed_kph", sa.Numeric(precision=7, scale=2), nullable=True),
        schema="fact",
    )
    op.add_column(
        "context_event",
        sa.Column("wind_gust_kph", sa.Numeric(precision=7, scale=2), nullable=True),
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_context_event_apparent_temperature_has_unit"),
        "context_event",
        "apparent_temperature IS NULL OR temperature_unit IS NOT NULL",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_context_event_wind_nonnegative"),
        "context_event",
        "wind_speed_kph IS NULL OR wind_speed_kph >= 0",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_context_event_wind_gust_nonnegative"),
        "context_event",
        "wind_gust_kph IS NULL OR wind_gust_kph >= 0",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_context_event_weather_interval_ordered"),
        "context_event",
        "weather_interval_ended_at IS NULL OR weather_interval_ended_at >= weather_observed_at",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_context_event_weather_has_provenance"),
        "context_event",
        "(temperature IS NULL AND apparent_temperature IS NULL AND pressure IS NULL "
        "AND humidity_percent IS NULL AND precipitation IS NULL AND conditions IS NULL "
        "AND wind_speed_kph IS NULL AND wind_gust_kph IS NULL "
        "AND weather_provider IS NULL AND weather_observation_id IS NULL "
        "AND weather_observed_at IS NULL AND weather_interval_ended_at IS NULL "
        "AND weather_confidence IS NULL) OR "
        "(weather_provider IS NOT NULL AND weather_observed_at IS NOT NULL)",
        schema="fact",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_context_event_weather_has_provenance"),
        "context_event",
        schema="fact",
        type_="check",
    )
    for name in (
        "weather_interval_ordered",
        "wind_gust_nonnegative",
        "wind_nonnegative",
        "apparent_temperature_has_unit",
    ):
        op.drop_constraint(
            op.f(f"ck_context_event_{name}"), "context_event", schema="fact", type_="check"
        )
    for name in (
        "wind_gust_kph",
        "wind_speed_kph",
        "apparent_temperature",
        "weather_interval_ended_at",
    ):
        op.drop_column("context_event", name, schema="fact")
    op.create_check_constraint(
        op.f("ck_context_event_weather_has_provenance"),
        "context_event",
        "(temperature IS NULL AND pressure IS NULL AND humidity_percent IS NULL "
        "AND precipitation IS NULL AND conditions IS NULL "
        "AND weather_provider IS NULL AND weather_observation_id IS NULL "
        "AND weather_observed_at IS NULL AND weather_confidence IS NULL) OR "
        "(weather_provider IS NOT NULL AND weather_observed_at IS NOT NULL)",
        schema="fact",
    )

    for name in (
        "activity_location_longitude_range",
        "activity_location_latitude_range",
        "activity_location_coordinate_pair",
        "activity_environment_valid",
    ):
        op.drop_constraint(
            op.f(f"ck_garmin_activity_event_{name}"),
            "garmin_activity_event",
            schema="fact",
            type_="check",
        )
    for name in ("location_longitude", "location_latitude", "location_name", "environment"):
        op.drop_column("garmin_activity_event", name, schema="fact")
