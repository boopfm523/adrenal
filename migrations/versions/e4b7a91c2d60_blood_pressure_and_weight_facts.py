"""blood pressure and body weight recorded facts

Revision ID: e4b7a91c2d60
Revises: d10a7c3e5f21
Create Date: 2026-08-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7a91c2d60"
down_revision: Union[str, Sequence[str], None] = "d10a7c3e5f21"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def _event_columns() -> list[sa.Column]:
    return [
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
    ]


def _event_constraints(table: str) -> list[sa.Constraint]:
    return [
        sa.CheckConstraint(
            "recorded_at >= occurred_at", name=op.f(f"ck_{table}_recorded_after_occurred")
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name=op.f(f"ck_{table}_no_self_supersede"),
        ),
        sa.CheckConstraint(
            "utc_offset_minutes BETWEEN -720 AND 840",
            name=op.f(f"ck_{table}_offset_within_real_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f(f"fk_{table}_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            [f"fact.{table}.id"],
            name=op.f(f"fk_{table}_supersedes_id_{table}"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f(f"pk_{table}")),
    ]


def _event_indexes(table: str) -> None:
    op.create_index(op.f(f"ix_fact_{table}_owner_id"), table, ["owner_id"], schema="fact")
    op.create_index(f"ix_{table}_occurred_at", table, ["occurred_at"], schema="fact")
    op.create_index(
        f"uq_{table}_supersedes_once",
        table,
        ["supersedes_id"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )
    op.create_index(
        f"uq_{table}_provider_identity",
        table,
        ["source_type", "provider_id", "source_revision"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("provider_id IS NOT NULL"),
    )


def upgrade() -> None:
    bp_table = "blood_pressure_event"
    op.create_table(
        bp_table,
        sa.Column("systolic_mmhg", sa.SmallInteger(), nullable=False),
        sa.Column("diastolic_mmhg", sa.SmallInteger(), nullable=False),
        sa.Column("pulse_bpm", sa.SmallInteger(), nullable=True),
        *_event_columns(),
        sa.CheckConstraint(
            "systolic_mmhg BETWEEN 1 AND 500",
            name=op.f("ck_blood_pressure_event_systolic_structural_range"),
        ),
        sa.CheckConstraint(
            "diastolic_mmhg BETWEEN 1 AND 500",
            name=op.f("ck_blood_pressure_event_diastolic_structural_range"),
        ),
        sa.CheckConstraint(
            "pulse_bpm IS NULL OR pulse_bpm BETWEEN 1 AND 500",
            name=op.f("ck_blood_pressure_event_pulse_structural_range"),
        ),
        *_event_constraints(bp_table),
        schema="fact",
    )
    _event_indexes(bp_table)

    weight_table = "weight_event"
    op.create_table(
        weight_table,
        sa.Column("value", sa.Numeric(precision=10, scale=4), nullable=False),
        sa.Column("unit", sa.String(length=8), nullable=False),
        sa.Column("normalized_kg", sa.Numeric(precision=10, scale=4), nullable=False),
        *_event_columns(),
        sa.CheckConstraint(
            "value > 0 AND value <= 5000",
            name=op.f("ck_weight_event_value_structural_range"),
        ),
        sa.CheckConstraint(
            "normalized_kg > 0 AND normalized_kg <= 5000",
            name=op.f("ck_weight_event_normalized_kg_structural_range"),
        ),
        *_event_constraints(weight_table),
        schema="fact",
    )
    _event_indexes(weight_table)


def downgrade() -> None:
    op.drop_table("weight_event", schema="fact")
    op.drop_table("blood_pressure_event", schema="fact")
