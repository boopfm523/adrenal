"""link confirmed lab results to source PDF pages

Revision ID: c3a91e7b2f04
Revises: e4b7a91c2d60
Create Date: 2026-08-10

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op


revision: str = "c3a91e7b2f04"
down_revision: Union[str, Sequence[str], None] = "e4b7a91c2d60"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_unique_constraint(
        "uq_lab_document_id_owner",
        "lab_document",
        ["id", "owner_id"],
        schema="fact",
    )
    op.add_column(
        "lab_result", sa.Column("source_document_id", sa.Uuid(), nullable=True), schema="fact"
    )
    op.add_column(
        "lab_result", sa.Column("source_page_number", sa.Integer(), nullable=True), schema="fact"
    )
    op.create_foreign_key(
        "fk_lab_result_source_document_owner",
        "lab_result",
        "lab_document",
        ["source_document_id", "owner_id"],
        ["id", "owner_id"],
        source_schema="fact",
        referent_schema="fact",
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_lab_result_source_document_page_complete",
        "lab_result",
        "(source_document_id IS NULL AND source_page_number IS NULL) OR "
        "(source_document_id IS NOT NULL AND source_page_number BETWEEN 1 AND 100)",
        schema="fact",
    )
    op.create_index(
        op.f("ix_fact_lab_result_source_document_id"),
        "lab_result",
        ["source_document_id"],
        unique=False,
        schema="fact",
    )


def downgrade() -> None:
    op.drop_index(
        op.f("ix_fact_lab_result_source_document_id"),
        table_name="lab_result",
        schema="fact",
    )
    op.drop_constraint(
        "ck_lab_result_source_document_page_complete",
        "lab_result",
        schema="fact",
        type_="check",
    )
    op.drop_constraint(
        "fk_lab_result_source_document_owner",
        "lab_result",
        schema="fact",
        type_="foreignkey",
    )
    op.drop_column("lab_result", "source_page_number", schema="fact")
    op.drop_column("lab_result", "source_document_id", schema="fact")
    op.drop_constraint("uq_lab_document_id_owner", "lab_document", schema="fact", type_="unique")
