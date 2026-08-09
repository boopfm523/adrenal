"""private report artifacts

Revision ID: f42a0b9e3d51
Revises: e31f9a8d2c40
Create Date: 2026-08-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f42a0b9e3d51"
down_revision: Union[str, Sequence[str], None] = "e31f9a8d2c40"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_artifact",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("format", sa.String(length=8), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(sha256) = 64", name=op.f("ck_report_artifact_artifact_checksum_length")
        ),
        sa.CheckConstraint(
            "format IN ('pdf', 'csv', 'json')",
            name=op.f("ck_report_artifact_artifact_format_allowed"),
        ),
        sa.CheckConstraint("byte_size > 0", name=op.f("ck_report_artifact_artifact_nonempty")),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_report_artifact_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["ops.report_snapshot.id"],
            name=op.f("fk_report_artifact_snapshot_id_report_snapshot"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_artifact")),
        sa.UniqueConstraint(
            "snapshot_id", "format", name="uq_report_artifact_one_format_per_snapshot"
        ),
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_report_artifact_owner_id"), "report_artifact", ["owner_id"], schema="ops"
    )
    op.create_index(
        op.f("ix_ops_report_artifact_snapshot_id"), "report_artifact", ["snapshot_id"], schema="ops"
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_ops_report_artifact_snapshot_id"), table_name="report_artifact", schema="ops"
    )
    op.drop_index(
        op.f("ix_ops_report_artifact_owner_id"), table_name="report_artifact", schema="ops"
    )
    op.drop_table("report_artifact", schema="ops")
