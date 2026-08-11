"""add Garmin intraday metric metadata and query index

Revision ID: c7e8a9f0b123
Revises: b4f2c6d8e901
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c7e8a9f0b123"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "b4f2c6d8e901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "garmin_metric_event",
        sa.Column(
            "aggregation",
            sa.String(length=32),
            server_default=sa.text("'daily_summary'"),
            nullable=False,
        ),
        schema="fact",
    )
    op.add_column(
        "garmin_metric_event",
        sa.Column("sample_interval_seconds", sa.Integer(), nullable=True),
        schema="fact",
    )
    op.execute(
        """
        UPDATE fact.garmin_metric_event
        SET aggregation = CASE
            WHEN garmin_source_member = 'daily-summary' THEN 'daily_summary'
            WHEN period_end_at IS NOT NULL THEN 'interval'
            ELSE 'point'
        END
        """
    )
    op.alter_column("garmin_metric_event", "aggregation", server_default=None, schema="fact")
    op.create_check_constraint(
        op.f("ck_garmin_metric_event_metric_aggregation_valid"),
        "garmin_metric_event",
        "aggregation IN ('point', 'interval', 'daily_summary', 'provider_sample')",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_metric_event_sample_interval_positive"),
        "garmin_metric_event",
        "sample_interval_seconds IS NULL OR sample_interval_seconds > 0",
        schema="fact",
    )
    op.create_index(
        "ix_garmin_metric_owner_type_occurred",
        "garmin_metric_event",
        ["owner_id", "metric_type", "occurred_at"],
        unique=False,
        schema="fact",
    )


def downgrade() -> None:
    op.drop_index(
        "ix_garmin_metric_owner_type_occurred",
        table_name="garmin_metric_event",
        schema="fact",
    )
    op.drop_constraint(
        op.f("ck_garmin_metric_event_sample_interval_positive"),
        "garmin_metric_event",
        schema="fact",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_garmin_metric_event_metric_aggregation_valid"),
        "garmin_metric_event",
        schema="fact",
        type_="check",
    )
    op.drop_column("garmin_metric_event", "sample_interval_seconds", schema="fact")
    op.drop_column("garmin_metric_event", "aggregation", schema="fact")
