"""preserve original extraction candidates

Revision ID: 30d8157ab26f
Revises: 71a1dfc8cb3b
Create Date: 2026-08-09 13:42:35.892537

"""

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql


# revision identifiers, used by Alembic.
revision: str = "30d8157ab26f"
down_revision: Union[str, Sequence[str], None] = "71a1dfc8cb3b"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    """Upgrade schema."""
    op.add_column(
        "extraction_draft",
        sa.Column(
            "original_candidates",
            postgresql.JSONB(astext_type=sa.Text()),
            nullable=True,
        ),
        schema="ai",
    )


def downgrade() -> None:
    """Downgrade schema."""
    op.drop_column("extraction_draft", "original_candidates", schema="ai")
