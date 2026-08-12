"""durable private export jobs

Revision ID: bc4e6a8d0f32
Revises: ab3d5f7a9c21
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "bc4e6a8d0f32"  # pragma: allowlist secret - Alembic revision ID
down_revision: str | Sequence[str] | None = "ab3d5f7a9c21"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "private_export",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("job_id", sa.Uuid(), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("request_fingerprint_sha256", sa.String(length=64), nullable=False),
        sa.Column("include_ai", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        sa.Column(
            "include_sensitive", sa.Boolean(), server_default=sa.text("true"), nullable=False
        ),
        sa.Column("total_rows", sa.Integer(), nullable=True),
        sa.Column("processed_rows", sa.Integer(), server_default=sa.text("0"), nullable=False),
        sa.Column("relative_path", sa.String(length=500), nullable=True),
        sa.Column("sha256", sa.String(length=64), nullable=True),
        sa.Column("byte_size", sa.BigInteger(), nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("purged_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(request_fingerprint_sha256) = 64",
            name=op.f("ck_private_export_request_fingerprint_length"),
        ),
        sa.CheckConstraint(
            "processed_rows >= 0", name=op.f("ck_private_export_processed_rows_nonnegative")
        ),
        sa.CheckConstraint(
            "total_rows IS NULL OR total_rows >= 0",
            name=op.f("ck_private_export_total_rows_nonnegative"),
        ),
        sa.CheckConstraint(
            "total_rows IS NULL OR processed_rows <= total_rows",
            name=op.f("ck_private_export_progress_within_total"),
        ),
        sa.CheckConstraint(
            "(relative_path IS NULL AND sha256 IS NULL AND byte_size IS NULL) OR (relative_path IS NOT NULL AND sha256 IS NOT NULL AND byte_size > 0)",
            name=op.f("ck_private_export_artifact_metadata_complete"),
        ),
        sa.CheckConstraint(
            "sha256 IS NULL OR char_length(sha256) = 64",
            name=op.f("ck_private_export_artifact_hash_length"),
        ),
        sa.CheckConstraint(
            "expires_at > created_at", name=op.f("ck_private_export_expiry_after_creation")
        ),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["ops.job.id"],
            name=op.f("fk_private_export_job_id_job"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_private_export_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_private_export")),
        sa.UniqueConstraint("job_id", name=op.f("uq_private_export_job_id")),
        sa.UniqueConstraint("owner_id", "idempotency_key", name="uq_private_export_owner_key"),
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_private_export_owner_id"),
        "private_export",
        ["owner_id"],
        unique=False,
        schema="ops",
    )
    op.create_index(
        "ix_private_export_owner_created",
        "private_export",
        ["owner_id", "created_at"],
        unique=False,
        schema="ops",
    )
    op.create_index(
        "ix_private_export_expiry",
        "private_export",
        ["expires_at", "purged_at"],
        unique=False,
        schema="ops",
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT SELECT ON ops.private_export TO healthcurve_backup;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_index("ix_private_export_expiry", table_name="private_export", schema="ops")
    op.drop_index("ix_private_export_owner_created", table_name="private_export", schema="ops")
    op.drop_index(op.f("ix_ops_private_export_owner_id"), table_name="private_export", schema="ops")
    op.drop_table("private_export", schema="ops")
