"""allow a provider record to return to a historical revision

Revision ID: c1e5a7b9d240
Revises: b8d4e6f1a230
Create Date: 2026-08-21
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "c1e5a7b9d240"
down_revision: Union[str, Sequence[str], None] = "b8d4e6f1a230"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

EVENT_TABLES = (
    "blood_pressure_event",
    "context_event",
    "diary_event",
    "dose_event",
    "emergency_injection_event",
    "garmin_activity_event",
    "garmin_metric_event",
    "garmin_sleep_event",
    "lab_panel",
    "life_event",
    "meal_event",
    "symptom_event",
    "temperature_event",
    "weight_event",
)


def upgrade() -> None:
    for table in EVENT_TABLES:
        index_name = f"uq_{table}_provider_identity"
        op.drop_index(index_name, table_name=table, schema="fact")
        op.create_index(
            index_name,
            table,
            ["source_type", "provider_id", "source_revision", "supersedes_id"],
            unique=True,
            schema="fact",
            postgresql_where=sa.text("provider_id IS NOT NULL"),
            postgresql_nulls_not_distinct=True,
        )


def downgrade() -> None:
    for table in EVENT_TABLES:
        index_name = f"uq_{table}_provider_identity"
        op.drop_index(index_name, table_name=table, schema="fact")
        op.create_index(
            index_name,
            table,
            ["source_type", "provider_id", "source_revision"],
            unique=True,
            schema="fact",
            postgresql_where=sa.text("provider_id IS NOT NULL"),
        )
