"""add bounded Telegram conversation context

Revision ID: c8e4b7a2d519
Revises: f1c4a7e9d230
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "c8e4b7a2d519"
down_revision: Union[str, Sequence[str], None] = "f1c4a7e9d230"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_conversation_context",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column(
            "turns",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=False,
        ),
        sa.Column(
            "pending_intent",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
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
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.CheckConstraint(
            "jsonb_typeof(turns) = 'array'",
            name=op.f("ck_telegram_conversation_context_turns_array"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_conversation_context")),
        sa.UniqueConstraint(
            "owner_id",
            "chat_id",
            name="uq_telegram_context_owner_chat",
        ),
        schema="ai",
    )
    op.create_index(
        op.f("ix_ai_telegram_conversation_context_owner_id"),
        "telegram_conversation_context",
        ["owner_id"],
        unique=False,
        schema="ai",
    )
    op.create_index(
        "ix_telegram_context_expiry",
        "telegram_conversation_context",
        ["expires_at"],
        unique=False,
        schema="ai",
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON ai.telegram_conversation_context TO healthcurve_ai;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT SELECT
                    ON ai.telegram_conversation_context TO healthcurve_backup;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_index(
        "ix_telegram_context_expiry",
        table_name="telegram_conversation_context",
        schema="ai",
    )
    op.drop_index(
        op.f("ix_ai_telegram_conversation_context_owner_id"),
        table_name="telegram_conversation_context",
        schema="ai",
    )
    op.drop_table("telegram_conversation_context", schema="ai")
