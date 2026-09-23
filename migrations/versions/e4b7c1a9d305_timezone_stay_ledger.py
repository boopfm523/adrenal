"""timezone stay ledger

Purely additive. No existing row is read or rewritten, and an instant before the first
stay still resolves to owner.default_timezone, so applying this changes the
interpretation of nothing already recorded.

Revision ID: e4b7c1a9d305
Revises: b6d2e8f4a371
Create Date: 2026-09-23
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e4b7c1a9d305"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "b6d2e8f4a371"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "timezone_stay",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("source", sa.String(length=16), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_timezone_stay_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_timezone_stay")),
        sa.UniqueConstraint("owner_id", "started_at", name="uq_timezone_stay_owner_started_at"),
        schema="identity",
    )
    op.create_index(
        op.f("ix_identity_timezone_stay_owner_id"),
        "timezone_stay",
        ["owner_id"],
        unique=False,
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_identity_timezone_stay_owner_id"),
        table_name="timezone_stay",
        schema="identity",
    )
    op.drop_table("timezone_stay", schema="identity")
