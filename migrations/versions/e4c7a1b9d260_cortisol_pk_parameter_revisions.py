"""add immutable owner cortisol PK parameter revisions

Revision ID: e4c7a1b9d260
Revises: a7c3e9d1f620
Create Date: 2026-08-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4c7a1b9d260"
down_revision: Union[str, Sequence[str], None] = "a7c3e9d1f620"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "cortisol_pk_parameter_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("revision_number", sa.Integer(), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("elimination_half_life_hours", sa.Numeric(8, 4), nullable=False),
        sa.Column("peak_time_hours", sa.Numeric(8, 4), nullable=False),
        sa.Column("distribution_volume_liters", sa.Numeric(10, 4), nullable=False),
        sa.Column("oral_bioavailability", sa.Numeric(8, 6), nullable=False),
        sa.Column("source_revision", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "oral_bioavailability > 0 AND oral_bioavailability <= 1",
            name=op.f("ck_cortisol_pk_parameter_revision_cortisol_pk_bioavailability_bounded"),
        ),
        sa.CheckConstraint(
            "distribution_volume_liters BETWEEN 1 AND 500",
            name=op.f("ck_cortisol_pk_parameter_revision_cortisol_pk_volume_bounded"),
        ),
        sa.CheckConstraint(
            "elimination_half_life_hours BETWEEN 0.25 AND 12",
            name=op.f("ck_cortisol_pk_parameter_revision_cortisol_pk_half_life_bounded"),
        ),
        sa.CheckConstraint(
            "peak_time_hours BETWEEN 0.1 AND 8",
            name=op.f("ck_cortisol_pk_parameter_revision_cortisol_pk_peak_time_bounded"),
        ),
        sa.CheckConstraint(
            "revision_number > 0",
            name=op.f("ck_cortisol_pk_parameter_revision_cortisol_pk_revision_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            ondelete="CASCADE",
            name=op.f("fk_cortisol_pk_parameter_revision_owner_id_owner"),
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["ops.cortisol_pk_parameter_revision.id"],
            ondelete="RESTRICT",
            name=op.f(
                "fk_cortisol_pk_parameter_revision_supersedes_id_cortisol_pk_parameter_revision"
            ),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_cortisol_pk_parameter_revision")),
        sa.UniqueConstraint(
            "owner_id",
            "revision_number",
            name="uq_cortisol_pk_owner_revision",
        ),
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_cortisol_pk_parameter_revision_owner_id"),
        "cortisol_pk_parameter_revision",
        ["owner_id"],
        schema="ops",
    )
    op.create_index(
        "ix_cortisol_pk_owner_revision_desc",
        "cortisol_pk_parameter_revision",
        ["owner_id", sa.text("revision_number DESC")],
        schema="ops",
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai') THEN
                REVOKE ALL ON ops.cortisol_pk_parameter_revision FROM healthcurve_ai;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT SELECT ON ops.cortisol_pk_parameter_revision TO healthcurve_backup;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("cortisol_pk_parameter_revision", schema="ops")
