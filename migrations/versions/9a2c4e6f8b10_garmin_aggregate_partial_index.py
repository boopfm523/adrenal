"""add Garmin aggregate partial index

Revision ID: 9a2c4e6f8b10
Revises: 6f1c2a8d4b90
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9a2c4e6f8b10"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "6f1c2a8d4b90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

INDEX_NAME = "ix_garmin_metric_owner_aggregate_occurred"


def upgrade() -> None:
    # A populated installation can already have millions of provider samples. Build
    # without holding a write-blocking table lock for the duration.
    with op.get_context().autocommit_block():
        op.create_index(
            INDEX_NAME,
            "garmin_metric_event",
            ["owner_id", sa.text("occurred_at DESC"), "id"],
            unique=False,
            schema="fact",
            postgresql_where=sa.text("aggregation <> 'provider_sample'"),
            postgresql_concurrently=True,
        )


def downgrade() -> None:
    with op.get_context().autocommit_block():
        op.drop_index(
            INDEX_NAME,
            table_name="garmin_metric_event",
            schema="fact",
            postgresql_concurrently=True,
        )
