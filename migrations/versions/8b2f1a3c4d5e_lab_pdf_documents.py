"""lab PDF document metadata

Revision ID: 8b2f1a3c4d5e
Revises: 42e650cece24
Create Date: 2026-08-09

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "8b2f1a3c4d5e"
down_revision: Union[str, Sequence[str], None] = "42e650cece24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "lab_document",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("display_name", sa.String(length=255), nullable=False),
        sa.Column("media_type", sa.String(length=64), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("byte_size", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("rejection_reason", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("validated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "page_count IS NULL OR (page_count >= 1 AND page_count <= 100)",
            name=op.f("ck_lab_document_document_page_count_bounded"),
        ),
        sa.CheckConstraint(
            "char_length(sha256) = 64",
            name=op.f("ck_lab_document_document_sha256_length"),
        ),
        sa.CheckConstraint(
            "byte_size > 0 AND byte_size <= 26214400",
            name=op.f("ck_lab_document_document_size_bounded"),
        ),
        sa.CheckConstraint(
            "status <> 'rejected' OR rejection_reason IS NOT NULL",
            name=op.f("ck_lab_document_rejected_document_has_reason"),
        ),
        sa.CheckConstraint(
            "status <> 'deleted' OR deleted_at IS NOT NULL",
            name=op.f("ck_lab_document_deleted_document_timestamped"),
        ),
        sa.CheckConstraint(
            "status <> 'stored' OR (page_count IS NOT NULL AND validated_at IS NOT NULL)",
            name=op.f("ck_lab_document_stored_document_validated"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_lab_document_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lab_document")),
        schema="fact",
    )
    op.create_index(
        "ix_lab_document_owner_created",
        "lab_document",
        ["owner_id", "created_at"],
        unique=False,
        schema="fact",
    )
    op.create_index(
        op.f("ix_fact_lab_document_owner_id"),
        "lab_document",
        ["owner_id"],
        unique=False,
        schema="fact",
    )


def downgrade() -> None:
    op.drop_index(op.f("ix_fact_lab_document_owner_id"), table_name="lab_document", schema="fact")
    op.drop_index("ix_lab_document_owner_created", table_name="lab_document", schema="fact")
    op.drop_table("lab_document", schema="fact")
