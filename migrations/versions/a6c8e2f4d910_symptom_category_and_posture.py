"""add optional symptom tracking category and blood-pressure posture

Revision ID: a6c8e2f4d910
Revises: f9b2c4d6e810
Create Date: 2026-08-16
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "a6c8e2f4d910"
down_revision: Union[str, Sequence[str], None] = "f9b2c4d6e810"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        "symptom_event",
        sa.Column("tracking_category", sa.String(length=32), nullable=True),
        schema="fact",
    )
    op.add_column(
        "symptom_event",
        sa.Column("tracking_category_revision", sa.String(length=48), nullable=True),
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_symptom_event_tracking_category_versioned"),
        "symptom_event",
        "(tracking_category IS NULL AND tracking_category_revision IS NULL) OR "
        "(tracking_category IN ('glucocorticoid', 'mineralocorticoid', 'postural', "
        "'other') AND tracking_category_revision = 'symptom-tracking-category-v1')",
        schema="fact",
    )
    op.add_column(
        "blood_pressure_event",
        sa.Column("body_position", sa.String(length=16), nullable=True),
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_blood_pressure_event_body_position_supported"),
        "blood_pressure_event",
        "body_position IS NULL OR body_position IN ('lying', 'sitting', 'standing')",
        schema="fact",
    )


def downgrade() -> None:
    op.drop_constraint(
        op.f("ck_blood_pressure_event_body_position_supported"),
        "blood_pressure_event",
        schema="fact",
        type_="check",
    )
    op.drop_column("blood_pressure_event", "body_position", schema="fact")
    op.drop_constraint(
        op.f("ck_symptom_event_tracking_category_versioned"),
        "symptom_event",
        schema="fact",
        type_="check",
    )
    op.drop_column("symptom_event", "tracking_category_revision", schema="fact")
    op.drop_column("symptom_event", "tracking_category", schema="fact")
