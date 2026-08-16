"""add private chatbot conversations and messages

Revision ID: a7c3e9d1f620
Revises: e9f2a6c4b813
Create Date: 2026-08-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "a7c3e9d1f620"
down_revision: Union[str, Sequence[str], None] = "e9f2a6c4b813"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "chat_conversation",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column(
            "title", sa.String(length=120), server_default="New conversation", nullable=False
        ),
        sa.Column(
            "include_sensitive_text", sa.Boolean(), server_default=sa.text("false"), nullable=False
        ),
        sa.Column("rolling_summary", sa.Text(), nullable=True),
        sa.Column("retention_expires_at", sa.DateTime(timezone=True), nullable=True),
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
        sa.Column("last_message_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "char_length(title) BETWEEN 1 AND 120",
            name=op.f("ck_chat_conversation_title_length"),
        ),
        sa.CheckConstraint(
            "rolling_summary IS NULL OR char_length(rolling_summary) <= 3000",
            name=op.f("ck_chat_conversation_summary_length"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            ondelete="CASCADE",
            name=op.f("fk_chat_conversation_owner_id_owner"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_conversation")),
        schema="ai",
    )
    op.create_index(
        op.f("ix_ai_chat_conversation_owner_id"),
        "chat_conversation",
        ["owner_id"],
        schema="ai",
    )
    op.create_index(
        "ix_chat_conversation_owner_recent",
        "chat_conversation",
        ["owner_id", "last_message_at", "created_at"],
        schema="ai",
    )

    op.create_table(
        "chat_message",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("client_message_id", sa.String(length=128), nullable=True),
        sa.Column("generated_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("model_name", sa.String(length=120), nullable=True),
        sa.Column("model_digest", sa.String(length=128), nullable=True),
        sa.Column("prompt_version", sa.String(length=32), nullable=True),
        sa.Column("schema_version", sa.String(length=32), nullable=True),
        sa.Column("tool_versions", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_scope", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("source_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
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
        sa.CheckConstraint("sequence > 0", name=op.f("ck_chat_message_sequence_positive")),
        sa.CheckConstraint(
            "role <> 'user' OR (state = 'accepted' AND body IS NOT NULL "
            "AND char_length(body) BETWEEN 1 AND 8000)",
            name=op.f("ck_chat_message_user_message_valid"),
        ),
        sa.CheckConstraint(
            "state <> 'completed' OR (role = 'assistant' AND body IS NOT NULL "
            "AND char_length(body) BETWEEN 1 AND 32000)",
            name=op.f("ck_chat_message_completed_assistant_body"),
        ),
        sa.CheckConstraint(
            "state <> 'completed' OR (generated_at IS NOT NULL "
            "AND model_name IS NOT NULL AND char_length(model_name) > 0 "
            "AND model_digest IS NOT NULL AND char_length(model_digest) > 0 "
            "AND prompt_version IS NOT NULL AND char_length(prompt_version) > 0 "
            "AND schema_version IS NOT NULL AND char_length(schema_version) > 0 "
            "AND tool_versions IS NOT NULL AND jsonb_typeof(tool_versions) = 'object' "
            "AND source_manifest IS NOT NULL AND jsonb_typeof(source_manifest) = 'array' "
            "AND source_scope IS NOT NULL AND jsonb_typeof(source_scope) = 'object' "
            "AND source_fingerprint IS NOT NULL AND char_length(source_fingerprint) > 0)",
            name=op.f("ck_chat_message_completed_assistant_provenance"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai.chat_conversation.id"],
            ondelete="CASCADE",
            name=op.f("fk_chat_message_conversation_id_chat_conversation"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            ondelete="CASCADE",
            name=op.f("fk_chat_message_owner_id_owner"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_message")),
        sa.UniqueConstraint("conversation_id", "sequence", name="uq_chat_message_sequence"),
        sa.UniqueConstraint(
            "conversation_id", "client_message_id", name="uq_chat_message_client_id"
        ),
        schema="ai",
    )
    op.create_index(
        op.f("ix_ai_chat_message_conversation_id"),
        "chat_message",
        ["conversation_id"],
        schema="ai",
    )
    op.create_index(
        op.f("ix_ai_chat_message_owner_id"),
        "chat_message",
        ["owner_id"],
        schema="ai",
    )
    op.create_index(
        "ix_chat_message_owner_conversation",
        "chat_message",
        ["owner_id", "conversation_id", "sequence"],
        schema="ai",
    )

    op.create_table(
        "chat_tool_execution",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("conversation_id", sa.Uuid(), nullable=False),
        sa.Column("assistant_message_id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("tool_name", sa.String(length=80), nullable=False),
        sa.Column("tool_version", sa.String(length=32), nullable=False),
        sa.Column("validated_arguments", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("outcome", sa.String(length=16), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("result_fingerprint", sa.String(length=128), nullable=True),
        sa.Column("source_manifest", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("error_code", sa.String(length=64), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_ms IS NULL OR duration_ms >= 0",
            name=op.f("ck_chat_tool_execution_duration_nonnegative"),
        ),
        sa.CheckConstraint(
            "jsonb_typeof(validated_arguments) = 'object'",
            name=op.f("ck_chat_tool_execution_arguments_object"),
        ),
        sa.ForeignKeyConstraint(
            ["assistant_message_id"],
            ["ai.chat_message.id"],
            ondelete="CASCADE",
            name=op.f("fk_chat_tool_execution_assistant_message_id_chat_message"),
        ),
        sa.ForeignKeyConstraint(
            ["conversation_id"],
            ["ai.chat_conversation.id"],
            ondelete="CASCADE",
            name=op.f("fk_chat_tool_execution_conversation_id_chat_conversation"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            ondelete="CASCADE",
            name=op.f("fk_chat_tool_execution_owner_id_owner"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_chat_tool_execution")),
        schema="ai",
    )
    for column in ("conversation_id", "assistant_message_id", "owner_id"):
        op.create_index(
            op.f(f"ix_ai_chat_tool_execution_{column}"),
            "chat_tool_execution",
            [column],
            schema="ai",
        )
    op.create_index(
        "ix_chat_tool_message_created",
        "chat_tool_execution",
        ["assistant_message_id", "created_at"],
        schema="ai",
    )

    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai') THEN
                GRANT SELECT, INSERT, UPDATE, DELETE
                    ON ai.chat_conversation, ai.chat_message, ai.chat_tool_execution
                    TO healthcurve_ai;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT SELECT
                    ON ai.chat_conversation, ai.chat_message, ai.chat_tool_execution
                    TO healthcurve_backup;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.drop_table("chat_tool_execution", schema="ai")
    op.drop_table("chat_message", schema="ai")
    op.drop_table("chat_conversation", schema="ai")
