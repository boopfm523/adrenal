"""preserve Telegram message send time

Revision ID: e9f2a6c4b813
Revises: c8e4b7a2d519
Create Date: 2026-08-13
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e9f2a6c4b813"
down_revision: Union[str, Sequence[str], None] = "c8e4b7a2d519"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "telegram_update",
        sa.Column("provider_message_id", sa.BigInteger(), nullable=True),
        schema="ops",
    )
    op.add_column(
        "telegram_update",
        sa.Column("provider_sent_at", sa.DateTime(timezone=True), nullable=True),
        schema="ops",
    )
    op.add_column(
        "telegram_update",
        sa.Column(
            "reference_time_source",
            sa.String(length=32),
            server_default="processing_time_fallback",
            nullable=False,
        ),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_column("telegram_update", "reference_time_source", schema="ops")
    op.drop_column("telegram_update", "provider_sent_at", schema="ops")
    op.drop_column("telegram_update", "provider_message_id", schema="ops")
