"""add wake-anchored regimen dose slots

Revision ID: b8d4e6f1a230
Revises: a6c8e2f4d910
Create Date: 2026-08-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "b8d4e6f1a230"
down_revision: Union[str, Sequence[str], None] = "a6c8e2f4d910"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "regimen_dose_slot",
        sa.Column(
            "timing_mode",
            sa.String(length=16),
            nullable=False,
            server_default="fixed_time",
        ),
        schema="plan",
    )
    op.add_column(
        "regimen_dose_slot",
        sa.Column("reminder_local_time", sa.Time(), nullable=True),
        schema="plan",
    )
    op.alter_column(
        "regimen_dose_slot",
        "scheduled_local_time",
        existing_type=sa.Time(),
        nullable=True,
        schema="plan",
    )
    op.create_check_constraint(
        op.f("ck_regimen_dose_slot_timing_fields_match_mode"),
        "regimen_dose_slot",
        "(timing_mode = 'fixed_time' AND scheduled_local_time IS NOT NULL "
        "AND reminder_local_time IS NULL) OR "
        "(timing_mode = 'wake' AND scheduled_local_time IS NULL "
        "AND reminder_local_time IS NOT NULL)",
        schema="plan",
    )
    op.alter_column(
        "regimen_dose_slot",
        "timing_mode",
        existing_type=sa.String(length=16),
        server_default=None,
        schema="plan",
    )


def downgrade() -> None:
    op.execute(
        "UPDATE plan.regimen_dose_slot "
        "SET scheduled_local_time = reminder_local_time "
        "WHERE timing_mode = 'wake'"
    )
    op.drop_constraint(
        op.f("ck_regimen_dose_slot_timing_fields_match_mode"),
        "regimen_dose_slot",
        schema="plan",
        type_="check",
    )
    op.alter_column(
        "regimen_dose_slot",
        "scheduled_local_time",
        existing_type=sa.Time(),
        nullable=False,
        schema="plan",
    )
    op.drop_column("regimen_dose_slot", "reminder_local_time", schema="plan")
    op.drop_column("regimen_dose_slot", "timing_mode", schema="plan")
