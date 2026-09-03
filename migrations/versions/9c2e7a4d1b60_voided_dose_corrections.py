"""preserve corrections that void erroneous dose entries

Revision ID: 9c2e7a4d1b60
Revises: 4b7c2d9e1a30
Create Date: 2026-09-03
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "9c2e7a4d1b60"
down_revision: Union[str, Sequence[str], None] = "4b7c2d9e1a30"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "dose_event",
        sa.Column("voided", sa.Boolean(), server_default=sa.false(), nullable=False),
        schema="fact",
    )


def downgrade() -> None:
    op.drop_column("dose_event", "voided", schema="fact")
