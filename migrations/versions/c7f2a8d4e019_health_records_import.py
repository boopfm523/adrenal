"""external health-records import (ADR-0040)

Adds the import batch, identity envelope, append-only revision, and AI-visibility
tables, plus nullable provenance columns on lab panels and results. Every new column
is nullable and no existing row is rewritten, so owner-entered labs are unchanged.

``analytics.lab_results`` is redefined with the same columns and one extra row filter:
results imported from the external dataset are excluded unless the owner has opted the
``labs`` category in, and always once they are no longer in the source. The view's
column list does not change, so the analytics catalog version does not either.

Privileges: the AI role keeps no access to the imported-record tables and may only read
the visibility opt-ins, never change them. The analyst roles get nothing new; the view
owner reads the two tables the filter needs.

Revision ID: c7f2a8d4e019
Revises: e4b7c1a9d305
Create Date: 2026-11-20
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

# ruff: noqa: S608 -- view SQL is assembled only from constant identifiers in this file.

revision: str = "c7f2a8d4e019"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "e4b7c1a9d305"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

VIEW_OWNER = "healthcurve_analytics_owner"

_LAB_RESULTS_SELECT = """
        SELECT r.id AS result_id,
               r.panel_id,
               p.occurred_at AS collected_at,
               p.local_time AS collected_local,
               p.local_time::date AS local_date,
               p.timezone,
               p.reported_at,
               p.specimen_type,
               p.report_status,
               r.analyte_name,
               r.normalized_analyte_code,
               r.original_value,
               r.qualitative_result,
               r.original_unit,
               r.original_reference_range,
               r.abnormal_flag,
               r.normalized_value,
               r.normalized_unit
        FROM fact.lab_result r
        JOIN fact.lab_panel p ON p.id = r.panel_id
        WHERE NOT EXISTS (SELECT 1 FROM fact.lab_panel superseding
                          WHERE superseding.supersedes_id = p.id)
"""

_IMPORTED_LAB_FILTER = """
          AND (
            p.source_type <> 'health_records_import'
            OR (
              EXISTS (SELECT 1 FROM ops.health_record_ai_visibility v
                      WHERE v.owner_id = p.owner_id
                        AND v.category = 'labs'
                        AND v.enabled)
              AND EXISTS (SELECT 1 FROM fact.health_record h
                          WHERE h.owner_id = r.owner_id
                            AND h.dataset_table = 'lab_results'
                            AND h.record_id = r.source_record_id
                            AND h.state = 'present')
            )
          )
"""


def _replace_lab_results_view(body: str) -> None:
    # CREATE OR REPLACE keeps the view's grants and owner; the column list is identical.
    op.execute(f"CREATE OR REPLACE VIEW analytics.lab_results AS {body}")


def _if_role(role: str, statements: str) -> None:
    op.execute(
        f"""
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = '{role}') THEN
                {statements}
            END IF;
        END
        $$;
        """
    )


def upgrade() -> None:
    op.add_column(
        "lab_panel", sa.Column("panel_name", sa.String(300), nullable=True), schema="fact"
    )
    op.add_column(
        "lab_panel", sa.Column("source_organization", sa.String(300), nullable=True), schema="fact"
    )
    op.add_column(
        "lab_panel", sa.Column("ordered_by", sa.String(300), nullable=True), schema="fact"
    )
    op.add_column(
        "lab_result",
        sa.Column("source_normalized_name", sa.String(300), nullable=True),
        schema="fact",
    )
    op.add_column(
        "lab_result", sa.Column("source_record_id", sa.String(128), nullable=True), schema="fact"
    )
    op.create_index(
        "ix_lab_result_source_record",
        "lab_result",
        ["owner_id", "source_record_id"],
        schema="fact",
    )

    op.create_table(
        "health_record_import_batch",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_schema_version", sa.Integer(), nullable=False),
        sa.Column("dataset_generated_at", sa.String(64), nullable=False),
        sa.Column("source_exported_at", postgresql.JSONB(), nullable=False),
        sa.Column("snapshot_sha256", sa.String(64), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column(
            "completed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("counts", postgresql.JSONB(), nullable=False),
        sa.CheckConstraint(
            "char_length(snapshot_sha256) = 64",
            name=op.f("ck_health_record_import_batch_snapshot_sha256_length"),
        ),
        sa.CheckConstraint(
            "completed_at >= started_at",
            name=op.f("ck_health_record_import_batch_completed_after_started"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_health_record_import_batch_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_record_import_batch")),
        schema="fact",
    )
    op.create_index(
        op.f("ix_fact_health_record_import_batch_owner_id"),
        "health_record_import_batch",
        ["owner_id"],
        schema="fact",
    )
    op.create_index(
        "ix_health_record_batch_owner_completed",
        "health_record_import_batch",
        ["owner_id", "completed_at"],
        schema="fact",
    )

    batch_fk = "fact.health_record_import_batch.id"
    op.create_table(
        "health_record",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("dataset_table", sa.String(32), nullable=False),
        sa.Column("record_id", sa.String(128), nullable=False),
        sa.Column("source", sa.String(200), nullable=False),
        sa.Column("organization", sa.String(300), nullable=False),
        sa.Column("record_date", sa.Date(), nullable=True),
        sa.Column("date_text", sa.String(100), nullable=True),
        sa.Column("local_time", sa.DateTime(timezone=False), nullable=True),
        sa.Column("timezone", sa.String(64), nullable=True),
        sa.Column("timezone_assumed", sa.Boolean(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("category", sa.String(120), nullable=True),
        sa.Column("kind", sa.String(120), nullable=True),
        sa.Column("ai_category", sa.String(32), nullable=True),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("first_seen_batch_id", sa.Uuid(), nullable=False),
        sa.Column("last_seen_batch_id", sa.Uuid(), nullable=False),
        sa.Column("missing_since_batch_id", sa.Uuid(), nullable=True),
        sa.Column("current_revision", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "current_revision >= 1", name=op.f("ck_health_record_current_revision_positive")
        ),
        sa.CheckConstraint(
            "(state = 'present') = (missing_since_batch_id IS NULL)",
            name=op.f("ck_health_record_missing_state_has_batch"),
        ),
        sa.CheckConstraint(
            "dataset_table <> 'medications' OR ai_category IS NULL",
            name=op.f("ck_health_record_medications_never_ai_visible"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_health_record_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["first_seen_batch_id"],
            [batch_fk],
            name=op.f("fk_health_record_first_seen_batch_id_health_record_import_batch"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["last_seen_batch_id"],
            [batch_fk],
            name=op.f("fk_health_record_last_seen_batch_id_health_record_import_batch"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["missing_since_batch_id"],
            [batch_fk],
            name=op.f("fk_health_record_missing_since_batch_id_health_record_import_batch"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_record")),
        sa.UniqueConstraint(
            "owner_id", "dataset_table", "record_id", name="uq_health_record_identity"
        ),
        schema="fact",
    )
    op.create_index(
        "ix_health_record_owner_table_date",
        "health_record",
        ["owner_id", "dataset_table", "record_date"],
        schema="fact",
    )

    op.create_table(
        "health_record_revision",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("health_record_id", sa.Uuid(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("content_sha256", sa.String(64), nullable=False),
        sa.Column("provider", sa.String(300), nullable=True),
        sa.Column("ordered_by", sa.String(300), nullable=True),
        sa.Column("reading_provider", sa.String(300), nullable=True),
        sa.Column("performing_lab", sa.String(300), nullable=True),
        sa.Column("prescriber", sa.String(300), nullable=True),
        sa.Column("summary_text", sa.Text(), nullable=True),
        sa.Column("body_text", sa.Text(), nullable=True),
        sa.Column("file_paths", postgresql.JSONB(), nullable=False),
        sa.Column("source_row", postgresql.JSONB(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "revision >= 1", name=op.f("ck_health_record_revision_revision_positive")
        ),
        sa.CheckConstraint(
            "char_length(content_sha256) = 64",
            name=op.f("ck_health_record_revision_content_sha256_length"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_health_record_revision_owner_id_owner"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["health_record_id"],
            ["fact.health_record.id"],
            name=op.f("fk_health_record_revision_health_record_id_health_record"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["batch_id"],
            [batch_fk],
            name=op.f("fk_health_record_revision_batch_id_health_record_import_batch"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_health_record_revision")),
        sa.UniqueConstraint("health_record_id", "revision", name="uq_health_record_revision"),
        schema="fact",
    )
    op.create_index(
        "ix_health_record_revision_owner", "health_record_revision", ["owner_id"], schema="fact"
    )

    op.create_table(
        "health_record_ai_visibility",
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("category", sa.String(32), nullable=False),
        sa.Column("enabled", sa.Boolean(), nullable=False),
        sa.Column(
            "changed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_health_record_ai_visibility_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "owner_id", "category", name=op.f("pk_health_record_ai_visibility")
        ),
        schema="ops",
    )

    # The AI role reads all of fact and writes ops by default privileges. Imported
    # records are not for it, and it must never be able to opt a category in.
    _if_role(
        "healthcurve_ai",
        """
        REVOKE ALL ON fact.health_record_import_batch, fact.health_record,
            fact.health_record_revision FROM healthcurve_ai;
        REVOKE ALL ON ops.health_record_ai_visibility FROM healthcurve_ai;
        GRANT SELECT ON ops.health_record_ai_visibility TO healthcurve_ai;
        """,
    )
    # The view owner needs the two tables the lab-results filter consults. Analysts
    # read only the view, never these tables.
    _if_role(
        VIEW_OWNER,
        f"""
        GRANT SELECT ON fact.health_record, ops.health_record_ai_visibility
            TO {VIEW_OWNER};
        """,
    )
    _replace_lab_results_view(_LAB_RESULTS_SELECT + _IMPORTED_LAB_FILTER)


def downgrade() -> None:
    _replace_lab_results_view(_LAB_RESULTS_SELECT)
    _if_role(
        VIEW_OWNER,
        f"""
        REVOKE SELECT ON fact.health_record, ops.health_record_ai_visibility
            FROM {VIEW_OWNER};
        """,
    )
    op.drop_table("health_record_ai_visibility", schema="ops")
    op.drop_index(
        "ix_health_record_revision_owner", table_name="health_record_revision", schema="fact"
    )
    op.drop_table("health_record_revision", schema="fact")
    op.drop_index("ix_health_record_owner_table_date", table_name="health_record", schema="fact")
    op.drop_table("health_record", schema="fact")
    op.drop_index(
        "ix_health_record_batch_owner_completed",
        table_name="health_record_import_batch",
        schema="fact",
    )
    op.drop_index(
        op.f("ix_fact_health_record_import_batch_owner_id"),
        table_name="health_record_import_batch",
        schema="fact",
    )
    op.drop_table("health_record_import_batch", schema="fact")
    op.drop_index("ix_lab_result_source_record", table_name="lab_result", schema="fact")
    op.drop_column("lab_result", "source_record_id", schema="fact")
    op.drop_column("lab_result", "source_normalized_name", schema="fact")
    op.drop_column("lab_panel", "ordered_by", schema="fact")
    op.drop_column("lab_panel", "source_organization", schema="fact")
    op.drop_column("lab_panel", "panel_name", schema="fact")
