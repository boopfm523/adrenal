"""lab panels and results

Revision ID: 42e650cece24
Revises: 30d8157ab26f
Create Date: 2026-08-09 13:47:54.499714

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


# revision identifiers, used by Alembic.
revision: str = "42e650cece24"
down_revision: Union[str, Sequence[str], None] = "30d8157ab26f"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.create_table(
        "lab_panel",
        sa.Column("laboratory_name", sa.String(length=300), nullable=True),
        sa.Column("accession_id", sa.String(length=255), nullable=True),
        sa.Column("specimen_type", sa.String(length=255), nullable=True),
        sa.Column("report_status", sa.String(length=120), nullable=True),
        sa.Column("reported_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reported_local_time", sa.DateTime(), nullable=False),
        sa.Column("reported_timezone", sa.String(length=64), nullable=False),
        sa.Column("reported_utc_offset_minutes", sa.SmallInteger(), nullable=False),
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("local_time", sa.DateTime(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("utc_offset_minutes", sa.SmallInteger(), nullable=False),
        sa.Column(
            "recorded_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("source_type", sa.String(length=32), nullable=False),
        sa.Column("provider_id", sa.String(length=255), nullable=True),
        sa.Column("source_revision", sa.String(length=128), nullable=True),
        sa.Column("import_batch_id", sa.Uuid(), nullable=True),
        sa.Column("confirmation_state", sa.String(length=32), nullable=False),
        sa.Column("supersedes_id", sa.Uuid(), nullable=True),
        sa.Column("correction_reason", sa.String(length=500), nullable=True),
        sa.Column("notes", sa.String(length=2000), nullable=True),
        sa.CheckConstraint(
            "recorded_at >= occurred_at", name=op.f("ck_lab_panel_recorded_after_occurred")
        ),
        sa.CheckConstraint(
            "reported_at >= occurred_at", name=op.f("ck_lab_panel_report_after_specimen")
        ),
        sa.CheckConstraint(
            "reported_utc_offset_minutes BETWEEN -720 AND 840",
            name=op.f("ck_lab_panel_report_offset_within_real_range"),
        ),
        sa.CheckConstraint(
            "supersedes_id IS NULL OR supersedes_id <> id",
            name=op.f("ck_lab_panel_no_self_supersede"),
        ),
        sa.CheckConstraint(
            "utc_offset_minutes BETWEEN -720 AND 840",
            name=op.f("ck_lab_panel_offset_within_real_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_lab_panel_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["supersedes_id"],
            ["fact.lab_panel.id"],
            name=op.f("fk_lab_panel_supersedes_id_lab_panel"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lab_panel")),
        sa.UniqueConstraint("id", "owner_id", name="uq_lab_panel_id_owner"),
        schema="fact",
    )
    op.create_index(op.f("ix_fact_lab_panel_owner_id"), "lab_panel", ["owner_id"], schema="fact")
    op.create_index("ix_lab_panel_occurred_at", "lab_panel", ["occurred_at"], schema="fact")
    op.create_index(
        "uq_lab_panel_provider_identity",
        "lab_panel",
        ["source_type", "provider_id", "source_revision"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("provider_id IS NOT NULL"),
    )
    op.create_index(
        "uq_lab_panel_supersedes_once",
        "lab_panel",
        ["supersedes_id"],
        unique=True,
        schema="fact",
        postgresql_where=sa.text("supersedes_id IS NOT NULL"),
    )

    op.create_table(
        "lab_result",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("panel_id", sa.Uuid(), nullable=False),
        sa.Column("source_row_index", sa.Integer(), nullable=True),
        sa.Column("analyte_name", sa.Text(), nullable=False),
        sa.Column("original_value", sa.Text(), nullable=True),
        sa.Column("qualitative_result", sa.Text(), nullable=True),
        sa.Column("original_unit", sa.Text(), nullable=True),
        sa.Column("original_reference_range", sa.Text(), nullable=True),
        sa.Column("abnormal_flag", sa.Text(), nullable=True),
        sa.Column("normalized_analyte_code", sa.String(length=120), nullable=True),
        sa.Column("normalized_value", sa.Numeric(precision=24, scale=10), nullable=True),
        sa.Column("normalized_unit", sa.String(length=64), nullable=True),
        sa.Column("normalization_method", sa.String(length=120), nullable=True),
        sa.CheckConstraint(
            "normalized_value IS NULL OR (normalized_unit IS NOT NULL AND normalization_method IS NOT NULL)",
            name=op.f("ck_lab_result_normalized_value_has_provenance"),
        ),
        sa.CheckConstraint(
            "original_value IS NOT NULL OR qualitative_result IS NOT NULL",
            name=op.f("ck_lab_result_lab_result_has_source_value"),
        ),
        sa.CheckConstraint(
            "source_row_index IS NULL OR source_row_index >= 0",
            name=op.f("ck_lab_result_source_row_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["panel_id", "owner_id"],
            ["fact.lab_panel.id", "fact.lab_panel.owner_id"],
            name=op.f("fk_lab_result_panel_id_lab_panel"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_lab_result")),
        schema="fact",
    )
    op.create_index(op.f("ix_fact_lab_result_owner_id"), "lab_result", ["owner_id"], schema="fact")
    op.create_index(op.f("ix_fact_lab_result_panel_id"), "lab_result", ["panel_id"], schema="fact")
    op.create_index(
        "ix_lab_result_analyte", "lab_result", ["owner_id", "analyte_name"], schema="fact"
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_index("ix_lab_result_analyte", table_name="lab_result", schema="fact")
    op.drop_index(op.f("ix_fact_lab_result_panel_id"), table_name="lab_result", schema="fact")
    op.drop_index(op.f("ix_fact_lab_result_owner_id"), table_name="lab_result", schema="fact")
    op.drop_table("lab_result", schema="fact")
    op.drop_index("uq_lab_panel_supersedes_once", table_name="lab_panel", schema="fact")
    op.drop_index("uq_lab_panel_provider_identity", table_name="lab_panel", schema="fact")
    op.drop_index("ix_lab_panel_occurred_at", table_name="lab_panel", schema="fact")
    op.drop_index(op.f("ix_fact_lab_panel_owner_id"), table_name="lab_panel", schema="fact")
    op.drop_table("lab_panel", schema="fact")
