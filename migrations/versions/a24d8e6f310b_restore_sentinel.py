"""add deterministic non-health restore sentinel

Revision ID: a24d8e6f310b
Revises: f6d81a2c4b90
Create Date: 2026-08-10
"""

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a24d8e6f310b"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "f6d81a2c4b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "restore_sentinel",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("marker_version", sa.Integer(), nullable=False),
        sa.Column("original_decimal", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("corrected_decimal", sa.Numeric(precision=18, scale=6), nullable=False),
        sa.Column("source", sa.String(length=64), nullable=False),
        sa.Column("correction_source", sa.String(length=64), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("utc_offset_minutes", sa.SmallInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "corrected_decimal <> original_decimal",
            name=op.f("ck_restore_sentinel_restore_sentinel_correction_changes_value"),
        ),
        sa.CheckConstraint(
            "utc_offset_minutes BETWEEN -840 AND 840",
            name=op.f("ck_restore_sentinel_restore_sentinel_offset_bounded"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_restore_sentinel")),
        sa.UniqueConstraint("marker_version", name=op.f("uq_restore_sentinel_marker_version")),
        schema="ops",
    )
    op.bulk_insert(
        sa.table(
            "restore_sentinel",
            sa.column("id", sa.Uuid()),
            sa.column("marker_version", sa.Integer()),
            sa.column("original_decimal", sa.Numeric(18, 6)),
            sa.column("corrected_decimal", sa.Numeric(18, 6)),
            sa.column("source", sa.String(64)),
            sa.column("correction_source", sa.String(64)),
            sa.column("occurred_at", sa.DateTime(timezone=True)),
            sa.column("timezone", sa.String(64)),
            sa.column("utc_offset_minutes", sa.SmallInteger()),
            schema="ops",
        ),
        [
            {
                "id": uuid.UUID("7de965ba-2fed-4a17-9f85-b0d984f87849"),
                "marker_version": 1,
                "original_decimal": Decimal("17.031250"),
                "corrected_decimal": Decimal("19.062500"),
                "source": "synthetic_restore_drill",
                "correction_source": "synthetic_owner_correction",
                "occurred_at": datetime(2024, 2, 29, 17, 34, 56, 789000, tzinfo=UTC),
                "timezone": "America/New_York",
                "utc_offset_minutes": -300,
            }
        ],
        multiinsert=False,
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai') THEN
                REVOKE INSERT, UPDATE, DELETE, TRUNCATE
                    ON ops.restore_sentinel FROM healthcurve_ai;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT SELECT ON ops.restore_sentinel TO healthcurve_backup;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_table("restore_sentinel", schema="ops")
