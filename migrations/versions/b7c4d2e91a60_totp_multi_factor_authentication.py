"""totp multi-factor authentication

Revision ID: b7c4d2e91a60
Revises: f42a0b9e3d51
Create Date: 2026-08-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b7c4d2e91a60"
down_revision: Union[str, Sequence[str], None] = "f42a0b9e3d51"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "owner",
        sa.Column("mfa_enabled", sa.Boolean(), server_default=sa.text("false"), nullable=False),
        schema="identity",
    )
    op.add_column(
        "owner", sa.Column("mfa_last_totp_step", sa.BigInteger(), nullable=True), schema="identity"
    )
    op.create_table(
        "mfa_recovery_code",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("code_hash", sa.String(length=64), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("used_at", sa.DateTime(timezone=True), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_mfa_recovery_code_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_mfa_recovery_code")),
        sa.UniqueConstraint("owner_id", "code_hash", name="uq_mfa_recovery_owner_hash"),
        schema="identity",
    )
    op.create_index(
        op.f("ix_identity_mfa_recovery_code_owner_id"),
        "mfa_recovery_code",
        ["owner_id"],
        unique=False,
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_identity_mfa_recovery_code_owner_id"),
        table_name="mfa_recovery_code",
        schema="identity",
    )
    op.drop_table("mfa_recovery_code", schema="identity")
    op.drop_column("owner", "mfa_last_totp_step", schema="identity")
    op.drop_column("owner", "mfa_enabled", schema="identity")
