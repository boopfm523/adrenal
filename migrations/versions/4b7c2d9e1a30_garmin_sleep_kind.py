"""distinguish Garmin overnight sleep from naps

Revision ID: 4b7c2d9e1a30
Revises: 7e4a9c2d6f10
Create Date: 2026-08-31
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "4b7c2d9e1a30"
down_revision: Union[str, Sequence[str], None] = "7e4a9c2d6f10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "garmin_sleep_event",
        sa.Column(
            "sleep_kind",
            sa.String(length=16),
            server_default="overnight",
            nullable=False,
        ),
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_sleep_event_sleep_kind_valid"),
        "garmin_sleep_event",
        "sleep_kind IN ('overnight', 'nap')",
        schema="fact",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_garmin_sleep_event_sleep_kind_valid"),
        "garmin_sleep_event",
        schema="fact",
        type_="check",
    )
    op.drop_column("garmin_sleep_event", "sleep_kind", schema="fact")
