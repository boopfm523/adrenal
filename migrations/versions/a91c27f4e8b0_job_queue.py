"""durable PostgreSQL job queue

Revision ID: a91c27f4e8b0
Revises: d53209775117
Create Date: 2026-08-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a91c27f4e8b0"
down_revision: Union[str, Sequence[str], None] = "d53209775117"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "job",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("task", sa.String(length=120), nullable=False),
        sa.Column("payload", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("idempotency_key", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("priority", sa.SmallInteger(), nullable=False),
        sa.Column("attempt_count", sa.Integer(), nullable=False),
        sa.Column("max_attempts", sa.Integer(), nullable=False),
        sa.Column("run_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("lease_token", sa.Uuid(), nullable=True),
        sa.Column("worker_id", sa.String(length=120), nullable=True),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.CheckConstraint("attempt_count >= 0", name="ck_job_attempt_count_nonnegative"),
        sa.CheckConstraint("max_attempts BETWEEN 1 AND 100", name="ck_job_max_attempts_bounded"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_job")),
        sa.UniqueConstraint("task", "idempotency_key", name="uq_job_task_idempotency_key"),
        schema="ops",
    )
    op.create_index(
        "ix_job_claim",
        "job",
        ["status", "run_at", "priority", "created_at"],
        unique=False,
        schema="ops",
    )


def downgrade() -> None:
    op.drop_index("ix_job_claim", table_name="job", schema="ops")
    op.drop_table("job", schema="ops")
