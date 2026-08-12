"""durable Telegram scheduled-dose reminders

Revision ID: d4a7c9e2f610
Revises: bc4e6a8d0f32
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d4a7c9e2f610"
down_revision: Union[str, Sequence[str], None] = "bc4e6a8d0f32"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "telegram_dose_reminder",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("regimen_version_id", sa.Uuid(), nullable=False),
        sa.Column("slot_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("scheduled_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("state", sa.String(length=24), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("sent_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("draft_id", sa.Uuid(), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_telegram_dose_reminder_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["regimen_version_id"], ["plan.regimen_version.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["slot_id"], ["plan.regimen_dose_slot.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_dose_reminder")),
        sa.UniqueConstraint(
            "owner_id", "slot_id", "local_date", name="uq_telegram_dose_reminder_occurrence"
        ),
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_telegram_dose_reminder_due_at"),
        "telegram_dose_reminder",
        ["due_at"],
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_telegram_dose_reminder_owner_id"),
        "telegram_dose_reminder",
        ["owner_id"],
        schema="ops",
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT SELECT ON ops.telegram_dose_reminder TO healthcurve_backup;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_ops_telegram_dose_reminder_owner_id"),
        table_name="telegram_dose_reminder",
        schema="ops",
    )
    op.drop_index(
        op.f("ix_ops_telegram_dose_reminder_due_at"),
        table_name="telegram_dose_reminder",
        schema="ops",
    )
    op.drop_table("telegram_dose_reminder", schema="ops")
