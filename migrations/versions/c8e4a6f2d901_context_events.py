"""privacy-controlled location, timezone, and weather context

Revision ID: c8e4a6f2d901
Revises: b7c4d2e91a60
Create Date: 2026-08-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c8e4a6f2d901"
down_revision: Union[str, Sequence[str], None] = "b7c4d2e91a60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "context_event",
        sa.Column("location_precision", sa.String(length=16), nullable=False),
        sa.Column("coarse_location_label", sa.String(length=120), nullable=True),
        sa.Column("latitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column("longitude", sa.Numeric(precision=9, scale=6), nullable=True),
        sa.Column(
            "exact_location_consent",
            sa.Boolean(),
            server_default=sa.text("false"),
            nullable=False,
        ),
        sa.Column("weather_provider", sa.String(length=64), nullable=True),
        sa.Column("weather_observation_id", sa.String(length=255), nullable=True),
        sa.Column("weather_observed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("temperature", sa.Numeric(precision=6, scale=2), nullable=True),
        sa.Column("temperature_unit", sa.String(length=8), nullable=True),
        sa.Column("pressure", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("pressure_unit", sa.String(length=8), nullable=True),
        sa.Column("humidity_percent", sa.Numeric(precision=5, scale=2), nullable=True),
        sa.Column("precipitation", sa.Numeric(precision=8, scale=2), nullable=True),
        sa.Column("precipitation_unit", sa.String(length=8), nullable=True),
        sa.Column("conditions", sa.String(length=200), nullable=True),
        sa.Column("weather_confidence", sa.Numeric(precision=4, scale=3), nullable=True),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_time", sa.DateTime(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("utc_offset_minutes", sa.SmallInteger(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=255), nullable=True),
        sa.Column("source_revision", sa.String(length=128), nullable=True),
        sa.Column("import_batch_id", sa.Uuid(), nullable=True),
        sa.Column("confirmation_state", sa.String(length=32), nullable=False),
        sa.Column("correction_reason", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.CheckConstraint(
            "(latitude IS NULL) = (longitude IS NULL)",
            name=op.f("ck_context_event_context_coordinate_pair"),
        ),
        sa.CheckConstraint(
            "latitude IS NULL OR latitude BETWEEN -90 AND 90",
            name=op.f("ck_context_event_latitude_range"),
        ),
        sa.CheckConstraint(
            "longitude IS NULL OR longitude BETWEEN -180 AND 180",
            name=op.f("ck_context_event_longitude_range"),
        ),
        sa.CheckConstraint(
            "(location_precision = 'none' AND coarse_location_label IS NULL "
            "AND latitude IS NULL AND exact_location_consent = false) OR "
            "(location_precision = 'coarse' AND coarse_location_label IS NOT NULL "
            "AND latitude IS NULL AND exact_location_consent = false) OR "
            "(location_precision = 'exact' AND latitude IS NOT NULL "
            "AND exact_location_consent = true)",
            name=op.f("ck_context_event_location_precision_consent"),
        ),
        sa.CheckConstraint(
            "(temperature IS NULL) = (temperature_unit IS NULL)",
            name=op.f("ck_context_event_temperature_has_unit"),
        ),
        sa.CheckConstraint(
            "(pressure IS NULL) = (pressure_unit IS NULL)",
            name=op.f("ck_context_event_pressure_has_unit"),
        ),
        sa.CheckConstraint(
            "(precipitation IS NULL) = (precipitation_unit IS NULL)",
            name=op.f("ck_context_event_precipitation_has_unit"),
        ),
        sa.CheckConstraint(
            "humidity_percent IS NULL OR humidity_percent BETWEEN 0 AND 100",
            name=op.f("ck_context_event_humidity_range"),
        ),
        sa.CheckConstraint(
            "precipitation IS NULL OR precipitation >= 0",
            name=op.f("ck_context_event_precipitation_nonnegative"),
        ),
        sa.CheckConstraint(
            "weather_confidence IS NULL OR weather_confidence BETWEEN 0 AND 1",
            name=op.f("ck_context_event_weather_confidence_range"),
        ),
        sa.CheckConstraint(
            "(temperature IS NULL AND pressure IS NULL AND humidity_percent IS NULL "
            "AND precipitation IS NULL AND conditions IS NULL "
            "AND weather_provider IS NULL AND weather_observation_id IS NULL "
            "AND weather_observed_at IS NULL AND weather_confidence IS NULL) OR "
            "(weather_provider IS NOT NULL AND weather_observed_at IS NOT NULL)",
            name=op.f("ck_context_event_weather_has_provenance"),
        ),
        sa.CheckConstraint(
            "recorded_at >= occurred_at",
            name=op.f("ck_context_event_recorded_after_occurred"),
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name=op.f("ck_context_event_no_self_supersede"),
        ),
        sa.CheckConstraint(
            "utc_offset_minutes BETWEEN -720 AND 840",
            name=op.f("ck_context_event_offset_within_real_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_context_event_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["fact.context_event.id"],
            name=op.f("fk_context_event_supersedes_id_context_event"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_context_event")),
        schema="fact",
    )
    op.create_index(
        op.f("ix_fact_context_event_owner_id"),
        "context_event",
        ["owner_id"],
        unique=False,
        schema="fact",
    )
    op.create_index(
        "ix_context_event_occurred_at",
        "context_event",
        ["occurred_at"],
        unique=False,
        schema="fact",
    )
    op.create_index(
        "uq_context_event_provider_identity",
        "context_event",
        ["source_type", "provider_id", "source_revision"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("provider_id IS NOT NULL"),
    )
    op.create_index(
        "uq_context_event_supersedes_once",
        "context_event",
        ["supersedes_id"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_context_event_supersedes_once",
        table_name="context_event",
        schema="fact",
    )
    op.drop_index(
        "uq_context_event_provider_identity",
        table_name="context_event",
        schema="fact",
    )
    op.drop_index(
        "ix_context_event_occurred_at",
        table_name="context_event",
        schema="fact",
    )
    op.drop_index(
        op.f("ix_fact_context_event_owner_id"),
        table_name="context_event",
        schema="fact",
    )
    op.drop_table("context_event", schema="fact")
