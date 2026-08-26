"""allow private chat to read owner cortisol model parameters

Revision ID: 6d2f8a4c1b90
Revises: 5c9e1a7b3d20
Create Date: 2026-08-25
"""

from typing import Sequence, Union

from alembic import op

revision: str = "6d2f8a4c1b90"
down_revision: Union[str, Sequence[str], None] = "5c9e1a7b3d20"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai') THEN
                GRANT SELECT ON ops.cortisol_pk_parameter_revision TO healthcurve_ai;
                REVOKE INSERT, UPDATE, DELETE, TRUNCATE
                    ON ops.cortisol_pk_parameter_revision FROM healthcurve_ai;
            END IF;
        END
        $$;
        """
    )


def downgrade() -> None:
    op.execute(
        """
        DO $$
        BEGIN
            IF EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'healthcurve_ai') THEN
                REVOKE SELECT ON ops.cortisol_pk_parameter_revision FROM healthcurve_ai;
            END IF;
        END
        $$;
        """
    )
