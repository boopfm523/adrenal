"""add isolated Garmin Connect sync provenance and MVP fields

Revision ID: f6d81a2c4b90
Revises: c3a91e7b2f04
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


revision: str = "f6d81a2c4b90"
down_revision: Union[str, Sequence[str], None] = "c3a91e7b2f04"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


_EVENT_TABLES = ("garmin_metric_event", "garmin_sleep_event", "garmin_activity_event")
_AUTOMATIC_FACT_COUNT_QUERIES = (
    sa.text("SELECT count(*) FROM fact.garmin_metric_event WHERE garmin_sync_run_id IS NOT NULL"),
    sa.text("SELECT count(*) FROM fact.garmin_sleep_event WHERE garmin_sync_run_id IS NOT NULL"),
    sa.text("SELECT count(*) FROM fact.garmin_activity_event WHERE garmin_sync_run_id IS NOT NULL"),
)


def upgrade() -> None:
    op.create_table(
        "garmin_connection",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=40), nullable=False),
        sa.Column("connected_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("disconnected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("last_success_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("checkpoint_date", sa.Date(), nullable=True),
        sa.Column("capabilities", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("client_version", sa.String(length=32), nullable=False),
        sa.Column("last_error_code", sa.String(length=64), nullable=True),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_garmin_connection_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_garmin_connection")),
        sa.UniqueConstraint("owner_id", name=op.f("uq_garmin_connection_owner_id")),
        schema="ops",
    )
    op.create_table(
        "garmin_sync_run",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("requested_start_date", sa.Date(), nullable=False),
        sa.Column("requested_end_date", sa.Date(), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column("status", sa.String(length=32), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("counts", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("warning_codes", postgresql.JSONB(astext_type=sa.Text()), nullable=False),
        sa.Column("client_version", sa.String(length=32), nullable=False),
        sa.CheckConstraint(
            "requested_end_date >= requested_start_date",
            name=op.f("ck_garmin_sync_run_garmin_sync_date_ordered"),
        ),
        sa.CheckConstraint(
            "finished_at >= started_at",
            name=op.f("ck_garmin_sync_run_garmin_sync_time_ordered"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_garmin_sync_run_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_garmin_sync_run")),
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_garmin_sync_run_owner_id"),
        "garmin_sync_run",
        ["owner_id"],
        unique=False,
        schema="ops",
    )

    for table in _EVENT_TABLES:
        op.alter_column(table, "garmin_import_batch_id", nullable=True, schema="fact")
        op.add_column(
            table, sa.Column("garmin_sync_run_id", sa.Uuid(), nullable=True), schema="fact"
        )
        op.create_foreign_key(
            op.f(f"fk_{table}_garmin_sync_run_id_garmin_sync_run"),
            table,
            "garmin_sync_run",
            ["garmin_sync_run_id"],
            ["id"],
            source_schema="fact",
            referent_schema="ops",
            ondelete="RESTRICT",
        )
        op.create_index(
            op.f(f"ix_fact_{table}_garmin_sync_run_id"),
            table,
            ["garmin_sync_run_id"],
            unique=False,
            schema="fact",
        )

    op.create_check_constraint(
        op.f("ck_garmin_metric_event_garmin_metric_exactly_one_source"),
        "garmin_metric_event",
        "(garmin_import_batch_id IS NOT NULL AND garmin_sync_run_id IS NULL) OR "
        "(garmin_import_batch_id IS NULL AND garmin_sync_run_id IS NOT NULL)",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_sleep_event_garmin_sleep_exactly_one_source"),
        "garmin_sleep_event",
        "(garmin_import_batch_id IS NOT NULL AND garmin_sync_run_id IS NULL) OR "
        "(garmin_import_batch_id IS NULL AND garmin_sync_run_id IS NOT NULL)",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_activity_event_garmin_activity_exactly_one_source"),
        "garmin_activity_event",
        "(garmin_import_batch_id IS NOT NULL AND garmin_sync_run_id IS NULL) OR "
        "(garmin_import_batch_id IS NULL AND garmin_sync_run_id IS NOT NULL)",
        schema="fact",
    )

    op.drop_constraint(
        op.f("ck_garmin_sleep_event_sleep_has_explicit_bounds"),
        "garmin_sleep_event",
        schema="fact",
        type_="check",
    )
    op.add_column(
        "garmin_sleep_event",
        sa.Column("duration_seconds", sa.Integer(), nullable=True),
        schema="fact",
    )
    op.add_column(
        "garmin_sleep_event",
        sa.Column(
            "garmin_duration_source",
            sa.String(length=32),
            nullable=False,
            server_default="calculated_from_bounds",
        ),
        schema="fact",
    )
    op.add_column(
        "garmin_sleep_event", sa.Column("awakenings", sa.Integer(), nullable=True), schema="fact"
    )
    op.execute(
        "UPDATE fact.garmin_sleep_event "
        "SET duration_seconds = EXTRACT(EPOCH FROM (ended_at - occurred_at))::integer"
    )
    op.alter_column(
        "garmin_sleep_event",
        "garmin_duration_source",
        server_default=None,
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_sleep_event_sleep_stage_count_nonnegative"),
        "garmin_sleep_event",
        "stage_count >= 0",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_sleep_event_sleep_duration_nonnegative"),
        "garmin_sleep_event",
        "duration_seconds IS NULL OR duration_seconds >= 0",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_sleep_event_sleep_duration_source_valid"),
        "garmin_sleep_event",
        "garmin_duration_source IN ('provider', 'calculated_from_bounds')",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_garmin_sleep_event_awakenings_nonnegative"),
        "garmin_sleep_event",
        "awakenings IS NULL OR awakenings >= 0",
        schema="fact",
    )

    op.drop_constraint(
        op.f("ck_garmin_activity_event_distance_nonnegative"),
        "garmin_activity_event",
        schema="fact",
        type_="check",
    )
    op.add_column(
        "garmin_activity_event",
        sa.Column("distance_miles", sa.Numeric(precision=14, scale=4), nullable=True),
        schema="fact",
    )
    op.execute(
        "UPDATE fact.garmin_activity_event "
        "SET distance_miles = ROUND(distance_m / 1609.344, 4) WHERE distance_m IS NOT NULL"
    )
    op.drop_column("garmin_activity_event", "distance_m", schema="fact")
    op.create_check_constraint(
        op.f("ck_garmin_activity_event_distance_nonnegative"),
        "garmin_activity_event",
        "distance_miles IS NULL OR distance_miles >= 0",
        schema="fact",
    )


def downgrade() -> None:
    connection = op.get_bind()
    automatic_rows = sum(
        connection.execute(statement).scalar_one() for statement in _AUTOMATIC_FACT_COUNT_QUERIES
    )
    if automatic_rows:
        raise RuntimeError("cannot downgrade Garmin Connect provenance while automatic facts exist")

    op.drop_constraint(
        op.f("ck_garmin_activity_event_distance_nonnegative"),
        "garmin_activity_event",
        schema="fact",
        type_="check",
    )
    op.add_column(
        "garmin_activity_event",
        sa.Column("distance_m", sa.Numeric(precision=14, scale=3), nullable=True),
        schema="fact",
    )
    op.execute(
        "UPDATE fact.garmin_activity_event "
        "SET distance_m = ROUND(distance_miles * 1609.344, 3) WHERE distance_miles IS NOT NULL"
    )
    op.drop_column("garmin_activity_event", "distance_miles", schema="fact")
    op.create_check_constraint(
        op.f("ck_garmin_activity_event_distance_nonnegative"),
        "garmin_activity_event",
        "distance_m IS NULL OR distance_m >= 0",
        schema="fact",
    )

    for name in (
        "awakenings_nonnegative",
        "sleep_duration_source_valid",
        "sleep_duration_nonnegative",
        "sleep_stage_count_nonnegative",
    ):
        op.drop_constraint(
            op.f(f"ck_garmin_sleep_event_{name}"),
            "garmin_sleep_event",
            schema="fact",
            type_="check",
        )
    op.drop_column("garmin_sleep_event", "awakenings", schema="fact")
    op.drop_column("garmin_sleep_event", "garmin_duration_source", schema="fact")
    op.drop_column("garmin_sleep_event", "duration_seconds", schema="fact")
    op.create_check_constraint(
        op.f("ck_garmin_sleep_event_sleep_has_explicit_bounds"),
        "garmin_sleep_event",
        "stage_count >= 2",
        schema="fact",
    )

    for table, constraint in (
        ("garmin_metric_event", "garmin_metric_exactly_one_source"),
        ("garmin_sleep_event", "garmin_sleep_exactly_one_source"),
        ("garmin_activity_event", "garmin_activity_exactly_one_source"),
    ):
        op.drop_constraint(op.f(f"ck_{table}_{constraint}"), table, schema="fact", type_="check")
        op.drop_index(op.f(f"ix_fact_{table}_garmin_sync_run_id"), table_name=table, schema="fact")
        op.drop_constraint(
            op.f(f"fk_{table}_garmin_sync_run_id_garmin_sync_run"),
            table,
            schema="fact",
            type_="foreignkey",
        )
        op.drop_column(table, "garmin_sync_run_id", schema="fact")
        op.alter_column(table, "garmin_import_batch_id", nullable=False, schema="fact")

    op.drop_index(
        op.f("ix_ops_garmin_sync_run_owner_id"), table_name="garmin_sync_run", schema="ops"
    )
    op.drop_table("garmin_sync_run", schema="ops")
    op.drop_table("garmin_connection", schema="ops")
