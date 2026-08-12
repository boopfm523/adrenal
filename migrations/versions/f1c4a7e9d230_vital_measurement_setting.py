"""record home or provider setting for weight and blood pressure

Revision ID: f1c4a7e9d230
Revises: e7a2c4d6f810
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f1c4a7e9d230"
down_revision: Union[str, Sequence[str], None] = "e7a2c4d6f810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

TABLES = ("blood_pressure_event", "weight_event")


def upgrade() -> None:
    for table in TABLES:
        op.add_column(
            table,
            sa.Column(
                "measurement_setting",
                sa.String(length=16),
                server_default=sa.text("'home'"),
                nullable=False,
            ),
            schema="fact",
        )
        op.create_check_constraint(
            op.f(f"ck_{table}_measurement_setting"),
            table,
            "measurement_setting IN ('home', 'provider')",
            schema="fact",
        )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_constraint(
            op.f(f"ck_{table}_measurement_setting"),
            table,
            schema="fact",
            type_="check",
        )
        op.drop_column(table, "measurement_setting", schema="fact")
