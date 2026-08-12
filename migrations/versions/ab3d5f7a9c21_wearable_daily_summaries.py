"""add deterministic wearable daily summaries

Revision ID: ab3d5f7a9c21
Revises: 9a2c4e6f8b10
Create Date: 2026-08-12
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "ab3d5f7a9c21"  # pragma: allowlist secret - Alembic revision ID
down_revision: Union[str, Sequence[str], None] = "9a2c4e6f8b10"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "wearable_daily_summary",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("local_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("metric_type", sa.String(length=48), nullable=False),
        sa.Column("unit", sa.String(length=32), nullable=True),
        sa.Column("sample_count", sa.Integer(), nullable=False),
        sa.Column("samples_without_cadence", sa.Integer(), nullable=False),
        sa.Column("observed_coverage_minutes", sa.Numeric(14, 4), nullable=False),
        sa.Column("observed_coverage_percent", sa.Numeric(8, 4), nullable=False),
        sa.Column("gap_count", sa.Integer(), nullable=True),
        sa.Column("largest_gap_minutes", sa.Numeric(14, 4), nullable=True),
        sa.Column("missingness_state", sa.String(length=40), nullable=False),
        sa.Column("incompatible_units", sa.Boolean(), nullable=False),
        sa.Column("minimum", sa.Numeric(14, 4), nullable=True),
        sa.Column("average", sa.Numeric(14, 4), nullable=True),
        sa.Column("maximum", sa.Numeric(14, 4), nullable=True),
        sa.Column("source_revision_watermark_sha256", sa.String(length=64), nullable=False),
        sa.Column("summary_version", sa.String(length=40), nullable=False),
        sa.Column(
            "refreshed_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "gap_count IS NULL OR gap_count >= 0",
            name=op.f("ck_wearable_daily_summary_wearable_summary_gap_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "largest_gap_minutes IS NULL OR largest_gap_minutes >= 0",
            name=op.f("ck_wearable_daily_summary_wearable_summary_largest_gap_nonnegative"),
        ),
        sa.CheckConstraint(
            "missingness_state IN ('no_samples', 'cadence_unavailable', "
            "'partial_observed_coverage', 'full_observed_coverage')",
            name=op.f("ck_wearable_daily_summary_wearable_summary_missingness_valid"),
        ),
        sa.CheckConstraint(
            "observed_coverage_minutes >= 0",
            name=op.f("ck_wearable_daily_summary_wearable_summary_coverage_minutes_nonnegative"),
        ),
        sa.CheckConstraint(
            "observed_coverage_percent BETWEEN 0 AND 100",
            name=op.f("ck_wearable_daily_summary_wearable_summary_coverage_percent_bounded"),
        ),
        sa.CheckConstraint(
            "sample_count >= 0",
            name=op.f("ck_wearable_daily_summary_wearable_summary_sample_count_nonnegative"),
        ),
        sa.CheckConstraint(
            "samples_without_cadence BETWEEN 0 AND sample_count",
            name=op.f("ck_wearable_daily_summary_wearable_summary_missing_cadence_bounded"),
        ),
        sa.CheckConstraint(
            "source_revision_watermark_sha256 ~ '^[0-9a-f]{64}$'",
            name=op.f("ck_wearable_daily_summary_wearable_summary_watermark_sha256"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_wearable_daily_summary_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_wearable_daily_summary")),
        sa.UniqueConstraint(
            "owner_id",
            "local_date",
            "timezone",
            "metric_type",
            "summary_version",
            name="uq_wearable_daily_summary_identity",
        ),
        schema="ops",
    )
    op.create_index(
        "ix_wearable_daily_summary_owner_date",
        "wearable_daily_summary",
        ["owner_id", "local_date", "timezone", "summary_version"],
        schema="ops",
    )

    # Immutable revisions are inserts. Statement-level transition tables invalidate
    # only dates/metrics touched by the write, including a superseded row whose
    # timestamp or metric type changed in the replacement.
    op.execute(
        """
        CREATE FUNCTION ops.invalidate_wearable_daily_summary_insert()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, ops, fact
        AS $$
        BEGIN
            DELETE FROM ops.wearable_daily_summary AS summary
            WHERE EXISTS (
                SELECT 1
                FROM inserted_garmin_metric AS inserted
                LEFT JOIN fact.garmin_metric_event AS prior
                    ON prior.id = inserted.supersedes_id
                WHERE summary.owner_id = inserted.owner_id
                  AND summary.summary_version = 'hc-wearable-daily-v1'
                  AND (
                    (
                      inserted.aggregation = 'provider_sample'
                      AND summary.metric_type = inserted.metric_type
                      AND summary.local_date =
                          (inserted.occurred_at AT TIME ZONE summary.timezone)::date
                    )
                    OR
                    (
                      prior.aggregation = 'provider_sample'
                      AND summary.metric_type = prior.metric_type
                      AND summary.local_date =
                          (prior.occurred_at AT TIME ZONE summary.timezone)::date
                    )
                  )
            );
            RETURN NULL;
        END
        $$;

        CREATE TRIGGER invalidate_wearable_daily_summary_after_insert
        AFTER INSERT ON fact.garmin_metric_event
        REFERENCING NEW TABLE AS inserted_garmin_metric
        FOR EACH STATEMENT
        EXECUTE FUNCTION ops.invalidate_wearable_daily_summary_insert();

        CREATE FUNCTION ops.invalidate_wearable_daily_summary_delete()
        RETURNS trigger
        LANGUAGE plpgsql
        SECURITY DEFINER
        SET search_path = pg_catalog, ops, fact
        AS $$
        BEGIN
            DELETE FROM ops.wearable_daily_summary AS summary
            WHERE EXISTS (
                SELECT 1
                FROM deleted_garmin_metric AS deleted
                WHERE deleted.aggregation = 'provider_sample'
                  AND summary.owner_id = deleted.owner_id
                  AND summary.metric_type = deleted.metric_type
                  AND summary.summary_version = 'hc-wearable-daily-v1'
                  AND summary.local_date =
                      (deleted.occurred_at AT TIME ZONE summary.timezone)::date
            );
            RETURN NULL;
        END
        $$;

        CREATE TRIGGER invalidate_wearable_daily_summary_after_delete
        AFTER DELETE ON fact.garmin_metric_event
        REFERENCING OLD TABLE AS deleted_garmin_metric
        FOR EACH STATEMENT
        EXECUTE FUNCTION ops.invalidate_wearable_daily_summary_delete();

        REVOKE ALL ON FUNCTION ops.invalidate_wearable_daily_summary_insert() FROM PUBLIC;
        REVOKE ALL ON FUNCTION ops.invalidate_wearable_daily_summary_delete() FROM PUBLIC;
        """
    )
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai') THEN
                GRANT SELECT ON ops.wearable_daily_summary TO healthcurve_ai;
                REVOKE INSERT, UPDATE, DELETE, TRUNCATE
                    ON ops.wearable_daily_summary FROM healthcurve_ai;
            END IF;
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_backup') THEN
                GRANT SELECT ON ops.wearable_daily_summary TO healthcurve_backup;
            END IF;
        END
        $$
        """
    )


def downgrade() -> None:
    op.execute(
        "DROP TRIGGER IF EXISTS invalidate_wearable_daily_summary_after_delete "
        "ON fact.garmin_metric_event"
    )
    op.execute(
        "DROP TRIGGER IF EXISTS invalidate_wearable_daily_summary_after_insert "
        "ON fact.garmin_metric_event"
    )
    op.execute("DROP FUNCTION IF EXISTS ops.invalidate_wearable_daily_summary_delete()")
    op.execute("DROP FUNCTION IF EXISTS ops.invalidate_wearable_daily_summary_insert()")
    op.drop_index(
        "ix_wearable_daily_summary_owner_date",
        table_name="wearable_daily_summary",
        schema="ops",
    )
    op.drop_table("wearable_daily_summary", schema="ops")
