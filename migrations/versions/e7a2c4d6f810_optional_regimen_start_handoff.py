"""allow draft regimen dates to resolve during activation

Revision ID: e7a2c4d6f810
Revises: d4a7c9e2f610
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "e7a2c4d6f810"
down_revision: Union[str, Sequence[str], None] = "d4a7c9e2f610"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_regimen_version_approved_requires_provenance"),
        "regimen_version",
        schema="plan",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_regimen_version_effective_range_ordered"),
        "regimen_version",
        schema="plan",
        type_="check",
    )
    op.alter_column(
        "regimen_version",
        "effective_from",
        existing_type=sa.DateTime(),
        nullable=True,
        schema="plan",
    )
    op.create_check_constraint(
        op.f("ck_regimen_version_effective_range_ordered"),
        "regimen_version",
        "effective_to IS NULL OR effective_from IS NULL OR effective_to > effective_from",
        schema="plan",
    )
    op.create_check_constraint(
        op.f("ck_regimen_version_approved_requires_provenance"),
        "regimen_version",
        "status <> 'approved' OR (approved_at IS NOT NULL AND approved_by IS NOT NULL "
        "AND effective_from IS NOT NULL)",
        schema="plan",
    )


def downgrade() -> None:
    connection = op.get_bind()
    pending = connection.scalar(
        sa.text("SELECT count(*) FROM plan.regimen_version WHERE effective_from IS NULL")
    )
    if pending:
        raise RuntimeError(
            "cannot downgrade while plan drafts without an effective start exist; "
            "set or delete those drafts first"
        )
    op.drop_constraint(
        op.f("ck_regimen_version_approved_requires_provenance"),
        "regimen_version",
        schema="plan",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_regimen_version_effective_range_ordered"),
        "regimen_version",
        schema="plan",
        type_="check",
    )
    op.alter_column(
        "regimen_version",
        "effective_from",
        existing_type=sa.DateTime(),
        nullable=False,
        schema="plan",
    )
    op.create_check_constraint(
        op.f("ck_regimen_version_effective_range_ordered"),
        "regimen_version",
        "effective_to IS NULL OR effective_to > effective_from",
        schema="plan",
    )
    op.create_check_constraint(
        op.f("ck_regimen_version_approved_requires_provenance"),
        "regimen_version",
        "status <> 'approved' OR (approved_at IS NOT NULL AND approved_by IS NOT NULL)",
        schema="plan",
    )
