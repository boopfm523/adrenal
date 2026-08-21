"""store the owner-selected Garmin sync lookback

Revision ID: d3a7c9e1f240
Revises: c1e5a7b9d240
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d3a7c9e1f240"
down_revision: Union[str, Sequence[str], None] = "c1e5a7b9d240"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "garmin_connection",
        sa.Column("sync_lookback_days", sa.Integer(), server_default="3", nullable=False),
        schema="ops",
    )
    op.create_check_constraint(
        "ck_garmin_connection_sync_lookback_days",
        "garmin_connection",
        "sync_lookback_days >= 1 AND sync_lookback_days <= 31",
        schema="ops",
    )


def downgrade() -> None:
    op.drop_constraint(
        "ck_garmin_connection_sync_lookback_days",
        "garmin_connection",
        schema="ops",
        type_="check",
    )
    op.drop_column("garmin_connection", "sync_lookback_days", schema="ops")
