"""add owner-scoped meal event facts

Revision ID: f9b2c4d6e810
Revises: e4c7a1b9d260
Create Date: 2026-08-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f9b2c4d6e810"
down_revision: Union[str, Sequence[str], None] = "e4c7a1b9d260"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = "meal_event"
    op.create_table(
        table,
        sa.Column("size", sa.String(length=8), nullable=True),
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
            "size IS NULL OR size IN ('xs', 's', 'm', 'l', 'xl', 'xxl')",
            name=op.f("ck_meal_event_size_supported"),
        ),
        sa.CheckConstraint(
            "recorded_at >= occurred_at", name=op.f("ck_meal_event_recorded_after_occurred")
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name=op.f("ck_meal_event_no_self_supersede"),
        ),
        sa.CheckConstraint(
            "utc_offset_minutes BETWEEN -720 AND 840",
            name=op.f("ck_meal_event_offset_within_real_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_meal_event_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["fact.meal_event.id"],
            name=op.f("fk_meal_event_supersedes_id_meal_event"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meal_event")),
        schema="fact",
    )
    op.create_index(op.f("ix_fact_meal_event_owner_id"), table, ["owner_id"], schema="fact")
    op.create_index("ix_meal_event_occurred_at", table, ["occurred_at"], schema="fact")
    op.create_index(
        "uq_meal_event_supersedes_once",
        table,
        ["supersedes_id"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )
    op.create_index(
        "uq_meal_event_provider_identity",
        table,
        ["source_type", "provider_id", "source_revision"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("provider_id IS NOT NULL"),
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai') THEN
                GRANT SELECT ON fact.meal_event TO healthcurve_ai;
                REVOKE INSERT, UPDATE, DELETE, TRUNCATE ON fact.meal_event FROM healthcurve_ai;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT SELECT ON fact.meal_event TO healthcurve_backup;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("meal_event", schema="fact")
