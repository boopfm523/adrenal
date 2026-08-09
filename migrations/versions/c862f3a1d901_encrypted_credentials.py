"""encrypted integration credentials

Revision ID: c862f3a1d901
Revises: a91c27f4e8b0
Create Date: 2026-08-09
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c862f3a1d901"
down_revision: Union[str, Sequence[str], None] = "a91c27f4e8b0"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "integration_credential",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("key_id", sa.String(length=64), nullable=False),
        sa.Column("cipher_version", sa.Integer(), nullable=False),
        sa.Column("nonce", sa.LargeBinary(), nullable=False),
        sa.Column("ciphertext", sa.LargeBinary(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "octet_length(ciphertext) >= 16",
            name="ck_integration_credential_credential_ciphertext_has_tag",
        ),
        sa.CheckConstraint(
            "octet_length(nonce) = 12", name="ck_integration_credential_credential_nonce_length"
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_integration_credential_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_integration_credential")),
        sa.UniqueConstraint(
            "owner_id", "provider", "name", name="uq_credential_owner_provider_name"
        ),
        schema="identity",
    )
    op.create_index(
        op.f("ix_identity_integration_credential_owner_id"),
        "integration_credential",
        ["owner_id"],
        unique=False,
        schema="identity",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_identity_integration_credential_owner_id"),
        table_name="integration_credential",
        schema="identity",
    )
    op.drop_table("integration_credential", schema="identity")
