"""rename the 100 mg emergency injection formulation

Revision ID: 5c9e1a7b3d20
Revises: d3a7c9e1f240
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "5c9e1a7b3d20"
down_revision: Union[str, Sequence[str], None] = "d3a7c9e1f240"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    medication = sa.table(
        "medication",
        sa.column("name", sa.String()),
        sa.column("normalized_name", sa.String()),
        sa.column("strength", sa.Numeric()),
        sa.column("strength_unit", sa.String()),
        schema="plan",
    )
    op.execute(
        medication.update()
        .where(
            medication.c.normalized_name == "hydrocortisone sodium succinate",
            medication.c.strength == 100,
            medication.c.strength_unit == "mg",
        )
        .values(
            name="Hydrocortisone Inj Dose",
            normalized_name="hydrocortisone inj dose",
        )
    )


def downgrade() -> None:
    medication = sa.table(
        "medication",
        sa.column("name", sa.String()),
        sa.column("normalized_name", sa.String()),
        sa.column("formulation", sa.String()),
        sa.column("strength", sa.Numeric()),
        sa.column("strength_unit", sa.String()),
        schema="plan",
    )
    op.execute(
        medication.update()
        .where(
            medication.c.normalized_name == "hydrocortisone inj dose",
            medication.c.formulation == "injection",
            medication.c.strength == 100,
            medication.c.strength_unit == "mg",
        )
        .values(
            name="Hydrocortisone sodium succinate",
            normalized_name="hydrocortisone sodium succinate",
        )
    )
