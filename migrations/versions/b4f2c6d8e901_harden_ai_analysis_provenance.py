"""harden AI analysis provenance

Revision ID: b4f2c6d8e901
Revises: a24d8e6f310b
Create Date: 2026-08-11
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b4f2c6d8e901"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "a24d8e6f310b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # Derived rows with incomplete legacy provenance must never become visible. Keep
    # them only as explicitly hidden, regenerable history; do not invent a model hash.
    op.add_column(
        "ai_analysis",
        sa.Column(
            "schema_version",
            sa.String(length=32),
            server_default=sa.text("'analysis-v1'"),
            nullable=False,
        ),
        schema="ai",
    )
    op.execute(
        """
        UPDATE ai.ai_analysis
        SET hidden_at = COALESCE(hidden_at, now()), model_digest = 'legacy-unknown'
        WHERE model_digest IS NULL OR model_digest = ''
        """
    )
    op.alter_column("ai_analysis", "model_digest", nullable=False, schema="ai")
    op.create_check_constraint(
        op.f("ck_ai_analysis_source_manifest_nonempty"),
        "ai_analysis",
        "jsonb_typeof(source_record_ids) = 'array' AND jsonb_array_length(source_record_ids) > 0",
        schema="ai",
    )
    op.create_check_constraint(
        op.f("ck_ai_analysis_body_nonempty"),
        "ai_analysis",
        "char_length(body) > 0",
        schema="ai",
    )
    op.create_check_constraint(
        op.f("ck_ai_analysis_model_digest_nonempty"),
        "ai_analysis",
        "char_length(model_digest) > 0",
        schema="ai",
    )
    op.create_check_constraint(
        op.f("ck_ai_analysis_schema_version_nonempty"),
        "ai_analysis",
        "char_length(schema_version) > 0",
        schema="ai",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_ai_analysis_schema_version_nonempty"),
        "ai_analysis",
        schema="ai",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_analysis_model_digest_nonempty"),
        "ai_analysis",
        schema="ai",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_analysis_body_nonempty"),
        "ai_analysis",
        schema="ai",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_ai_analysis_source_manifest_nonempty"),
        "ai_analysis",
        schema="ai",
        type_="check",
    )
    op.alter_column("ai_analysis", "model_digest", nullable=True, schema="ai")
    op.drop_column("ai_analysis", "schema_version", schema="ai")
