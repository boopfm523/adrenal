"""immutable report snapshots

Revision ID: e31f9a8d2c40
Revises: 8b2f1a3c4d5e
Create Date: 2026-08-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "e31f9a8d2c40"
down_revision: Union[str, Sequence[str], None] = "8b2f1a3c4d5e"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "report_snapshot",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("date_from", sa.Date(), nullable=False),
        sa.Column("date_to", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("selected_sections", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("include_ai", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column("source_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("metric_values", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("snapshot_content", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("render_version", sa.String(length=32), nullable=False),
        sa.Column("canonical_sha256", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "char_length(canonical_sha256) = 64",
            name=op.f("ck_report_snapshot_report_checksum_length"),
        ),
        sa.CheckConstraint(
            "date_to >= date_from", name=op.f("ck_report_snapshot_report_date_ordered")
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_report_snapshot_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_report_snapshot")),
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_report_snapshot_owner_id"), "report_snapshot", ["owner_id"], schema="ops"
    )
    op.create_index(
        "ix_report_snapshot_owner_created",
        "report_snapshot",
        ["owner_id", "created_at"],
        schema="ops",
    )


def downgrade() -> None:
    op.drop_index("ix_report_snapshot_owner_created", table_name="report_snapshot", schema="ops")
    op.drop_index(
        op.f("ix_ops_report_snapshot_owner_id"), table_name="report_snapshot", schema="ops"
    )
    op.drop_table("report_snapshot", schema="ops")
