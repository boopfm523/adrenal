"""preserve regimen effective-time timezone provenance

Revision ID: f18c2d7a9e40
Revises: a81d4f6c2e90
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "f18c2d7a9e40"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "a81d4f6c2e90"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    table = "regimen_version"
    schema = "plan"
    op.add_column(table, sa.Column("effective_timezone", sa.String(length=64)), schema=schema)
    op.add_column(table, sa.Column("effective_from_local", sa.DateTime()), schema=schema)
    op.add_column(table, sa.Column("effective_to_local", sa.DateTime()), schema=schema)
    op.add_column(
        table, sa.Column("effective_from_utc_offset_minutes", sa.SmallInteger()), schema=schema
    )
    op.add_column(
        table, sa.Column("effective_to_utc_offset_minutes", sa.SmallInteger()), schema=schema
    )
    op.add_column(
        table,
        sa.Column(
            "effective_time_provenance",
            sa.String(length=32),
            nullable=False,
            server_default="legacy_naive_utc_ambiguous",
        ),
        schema=schema,
    )
    op.create_check_constraint(
        op.f("ck_regimen_version_effective_from_offset_within_real_range"),
        table,
        "effective_from_utc_offset_minutes IS NULL OR "
        "effective_from_utc_offset_minutes BETWEEN -720 AND 840",
        schema=schema,
    )
    op.create_check_constraint(
        op.f("ck_regimen_version_effective_to_offset_within_real_range"),
        table,
        "effective_to_utc_offset_minutes IS NULL OR "
        "effective_to_utc_offset_minutes BETWEEN -720 AND 840",
        schema=schema,
    )
    op.alter_column(table, "effective_time_provenance", server_default=None, schema=schema)


def downgrade() -> None:
    table = "regimen_version"
    schema = "plan"
    op.drop_constraint(
        op.f("ck_regimen_version_effective_to_offset_within_real_range"),
        table,
        schema=schema,
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_regimen_version_effective_from_offset_within_real_range"),
        table,
        schema=schema,
        type_="check",
    )
    op.drop_column(table, "effective_time_provenance", schema=schema)
    op.drop_column(table, "effective_to_utc_offset_minutes", schema=schema)
    op.drop_column(table, "effective_from_utc_offset_minutes", schema=schema)
    op.drop_column(table, "effective_to_local", schema=schema)
    op.drop_column(table, "effective_from_local", schema=schema)
    op.drop_column(table, "effective_timezone", schema=schema)
