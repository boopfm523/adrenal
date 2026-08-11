"""add explicit Garmin sleep-stage intervals

Revision ID: a81d4f6c2e90
Revises: c7e8a9f0b123
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a81d4f6c2e90"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "c7e8a9f0b123"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "garmin_sleep_stage_interval",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("sleep_event_id", sa.Uuid(), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("stage", sa.String(length=24), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("ended_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "ordinal >= 0",
            name=op.f("ck_garmin_sleep_stage_interval_sleep_stage_ordinal_nonnegative"),
        ),
        sa.CheckConstraint(
            "ended_at > started_at",
            name=op.f("ck_garmin_sleep_stage_interval_sleep_stage_interval_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["sleep_event_id"],
            ["fact.garmin_sleep_event.id"],
            name=op.f("fk_garmin_sleep_stage_interval_sleep_event_id_garmin_sleep_event"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_garmin_sleep_stage_interval")),
        sa.UniqueConstraint(
            "sleep_event_id",
            "ordinal",
            name=op.f("uq_sleep_stage_interval_ordinal"),
        ),
        schema="fact",
    )
    op.create_index(
        op.f("ix_fact_garmin_sleep_stage_interval_sleep_event_id"),
        "garmin_sleep_stage_interval",
        ["sleep_event_id"],
        unique=False,
        schema="fact",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_fact_garmin_sleep_stage_interval_sleep_event_id"),
        table_name="garmin_sleep_stage_interval",
        schema="fact",
    )
    op.drop_table("garmin_sleep_stage_interval", schema="fact")
