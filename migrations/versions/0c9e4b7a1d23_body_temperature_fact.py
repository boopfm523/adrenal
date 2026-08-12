"""body temperature recorded facts

Revision ID: 0c9e4b7a1d23
Revises: f18c2d7a9e40
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0c9e4b7a1d23"
down_revision: Union[str, Sequence[str], None] = "f18c2d7a9e40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = "temperature_event"
    op.create_table(
        table,
        sa.Column("value", sa.Numeric(precision=6, scale=2), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("normalized_c", sa.Numeric(precision=6, scale=2), nullable=False),
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
            "normalized_c BETWEEN 25 AND 45",
            name=op.f("ck_temperature_event_human_measurement_range"),
        ),
        sa.CheckConstraint(
            "recorded_at >= occurred_at",
            name=op.f("ck_temperature_event_recorded_after_occurred"),
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name=op.f("ck_temperature_event_no_self_supersede"),
        ),
        sa.CheckConstraint(
            "utc_offset_minutes BETWEEN -720 AND 840",
            name=op.f("ck_temperature_event_offset_within_real_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_temperature_event_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["fact.temperature_event.id"],
            name=op.f("fk_temperature_event_supersedes_id_temperature_event"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_temperature_event")),
        schema="fact",
    )
    op.create_index(
        op.f("ix_fact_temperature_event_owner_id"),
        table,
        ["owner_id"],
        schema="fact",
    )
    op.create_index(
        "ix_temperature_event_occurred_at",
        table,
        ["occurred_at"],
        schema="fact",
    )
    op.create_index(
        "uq_temperature_event_supersedes_once",
        table,
        ["supersedes_id"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )
    op.create_index(
        "uq_temperature_event_provider_identity",
        table,
        ["source_type", "provider_id", "source_revision"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("provider_id IS NOT NULL"),
    )


def downgrade() -> None:
    op.drop_table("temperature_event", schema="fact")
