"""let the owner choose a local chat model per conversation

Revision ID: b6d2e8f4a371
Revises: 5e1a9c3d7b24
Create Date: 2026-09-14
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b6d2e8f4a371"
down_revision: Union[str, Sequence[str], None] = "5e1a9c3d7b24"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # NULL keeps the configured default model (HC_OLLAMA_MODEL). Existing table grants
    # cover the new column.
    op.add_column(
        "chat_conversation",
        sa.Column("model_name", sa.String(length=200), nullable=True),
        schema="ai",
    )


def downgrade() -> None:
    op.drop_column("chat_conversation", "model_name", schema="ai")
