"""one-time Telegram coarse location requests

Revision ID: d10a7c3e5f21
Revises: c8e4a6f2d901
Create Date: 2026-08-10
"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "d10a7c3e5f21"
down_revision: Union[str, Sequence[str], None] = "c8e4a6f2d901"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.drop_constraint(
        op.f("ck_context_event_location_precision_consent"),
        "context_event",
        schema="fact",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_context_event_location_precision_consent"),
        "context_event",
        "(location_precision = 'none' AND coarse_location_label IS NULL "
        "AND latitude IS NULL AND exact_location_consent = false) OR "
        "(location_precision = 'coarse' AND coarse_location_label IS NOT NULL "
        "AND exact_location_consent = false) OR "
        "(location_precision = 'exact' AND latitude IS NOT NULL "
        "AND exact_location_consent = true)",
        schema="fact",
    )
    op.create_check_constraint(
        op.f("ck_context_event_coarse_coordinates_rounded"),
        "context_event",
        "location_precision <> 'coarse' OR latitude IS NULL OR "
        "(latitude = round(latitude, 1) AND longitude = round(longitude, 1))",
        schema="fact",
    )

    op.create_table(
        "saved_coarse_location",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=32), nullable=False),
        sa.Column("label", sa.String(length=120), nullable=False),
        sa.Column("latitude", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("longitude", sa.Numeric(precision=4, scale=1), nullable=False),
        sa.Column("timezone", sa.String(length=64), nullable=False),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.CheckConstraint(
            "latitude = round(latitude, 1) AND longitude = round(longitude, 1)",
            name=op.f("ck_saved_coarse_location_coordinates_rounded"),
        ),
        sa.CheckConstraint(
            "latitude BETWEEN -90 AND 90",
            name=op.f("ck_saved_coarse_location_latitude_range"),
        ),
        sa.CheckConstraint(
            "longitude BETWEEN -180 AND 180",
            name=op.f("ck_saved_coarse_location_longitude_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_saved_coarse_location_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_saved_coarse_location")),
        sa.UniqueConstraint("owner_id", "name", name="uq_saved_coarse_location_owner_name"),
        schema="identity",
    )
    op.create_index(
        op.f("ix_identity_saved_coarse_location_owner_id"),
        "saved_coarse_location",
        ["owner_id"],
        schema="identity",
    )

    op.create_table(
        "telegram_location_request",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("owner_id", sa.Uuid(), nullable=False),
        sa.Column("chat_id", sa.BigInteger(), nullable=False),
        sa.Column("draft_id", sa.Uuid(), nullable=False),
        sa.Column("state", sa.String(length=16), nullable=False),
        sa.Column("rounded_latitude", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("rounded_longitude", sa.Numeric(precision=4, scale=1), nullable=True),
        sa.Column("location_label", sa.String(length=120), nullable=True),
        sa.Column(
            "requested_at",
            sa.DateTime(timezone=True),
            server_default=sa.text("now()"),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.CheckConstraint(
            "(rounded_latitude IS NULL) = (rounded_longitude IS NULL)",
            name=op.f("ck_telegram_location_request_coordinate_pair"),
        ),
        sa.CheckConstraint(
            "rounded_latitude IS NULL OR (rounded_latitude = round(rounded_latitude, 1) "
            "AND rounded_longitude = round(rounded_longitude, 1))",
            name=op.f("ck_telegram_location_request_coordinates_rounded"),
        ),
        sa.CheckConstraint(
            "rounded_latitude IS NULL OR rounded_latitude BETWEEN -90 AND 90",
            name=op.f("ck_telegram_location_request_latitude_range"),
        ),
        sa.CheckConstraint(
            "rounded_longitude IS NULL OR rounded_longitude BETWEEN -180 AND 180",
            name=op.f("ck_telegram_location_request_longitude_range"),
        ),
        sa.ForeignKeyConstraint(
            ["owner_id"],
            ["identity.owner.id"],
            name=op.f("fk_telegram_location_request_owner_id_owner"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_telegram_location_request")),
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_telegram_location_request_draft_id"),
        "telegram_location_request",
        ["draft_id"],
        schema="ops",
    )
    op.create_index(
        op.f("ix_ops_telegram_location_request_owner_id"),
        "telegram_location_request",
        ["owner_id"],
        schema="ops",
    )
    op.create_index(
        "uq_telegram_location_request_pending_owner",
        "telegram_location_request",
        ["owner_id"],
        unique=True,
        schema="ops",
        postgresql_where=sa.text("state IN ('pending', 'attached')"),
    )


def downgrade() -> None:
    op.drop_index(
        "uq_telegram_location_request_pending_owner",
        table_name="telegram_location_request",
        schema="ops",
    )
    op.drop_index(
        op.f("ix_ops_telegram_location_request_owner_id"),
        table_name="telegram_location_request",
        schema="ops",
    )
    op.drop_index(
        op.f("ix_ops_telegram_location_request_draft_id"),
        table_name="telegram_location_request",
        schema="ops",
    )
    op.drop_table("telegram_location_request", schema="ops")
    op.drop_index(
        op.f("ix_identity_saved_coarse_location_owner_id"),
        table_name="saved_coarse_location",
        schema="identity",
    )
    op.drop_table("saved_coarse_location", schema="identity")
    op.drop_constraint(
        op.f("ck_context_event_coarse_coordinates_rounded"),
        "context_event",
        schema="fact",
        type_="check",
    )
    op.drop_constraint(
        op.f("ck_context_event_location_precision_consent"),
        "context_event",
        schema="fact",
        type_="check",
    )
    op.create_check_constraint(
        op.f("ck_context_event_location_precision_consent"),
        "context_event",
        "(location_precision = 'none' AND coarse_location_label IS NULL "
        "AND latitude IS NULL AND exact_location_consent = false) OR "
        "(location_precision = 'coarse' AND coarse_location_label IS NOT NULL "
        "AND latitude IS NULL AND exact_location_consent = false) OR "
        "(location_precision = 'exact' AND latitude IS NOT NULL "
        "AND exact_location_consent = true)",
        schema="fact",
    )
