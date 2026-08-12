"""record Garmin sync request origin

Revision ID: 6f1c2a8d4b90
Revises: 0c9e4b7a1d23
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "6f1c2a8d4b90"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "0c9e4b7a1d23"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "garmin_sync_run",
        sa.Column(
            "origin",
            sa.String(length=24),
            server_default="legacy",
            nullable=False,
        ),
        schema="ops",
    )


def downgrade() -> None:
    op.drop_column("garmin_sync_run", "origin", schema="ops")
