"""Versioned owner assumptions for deterministic HealthCurve analytics."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import OPS_SCHEMA, OpsBase


class CortisolPkParameterRevision(OpsBase):
    """Immutable owner revision for the ``hc-wake-free-v3`` PK assumptions.

    These rows are modeling assumptions, not recorded health facts or a medication
    plan. Creating a replacement row leaves prior calculations explainable.
    """

    __tablename__ = "cortisol_pk_parameter_revision"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    revision_number: Mapped[int] = mapped_column(Integer, nullable=False)
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "ops.cortisol_pk_parameter_revision.id",
            ondelete="RESTRICT",
        )
    )
    elimination_half_life_hours: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    peak_time_hours: Mapped[Decimal] = mapped_column(Numeric(8, 4), nullable=False)
    distribution_volume_liters: Mapped[Decimal] = mapped_column(Numeric(10, 4), nullable=False)
    oral_bioavailability: Mapped[Decimal] = mapped_column(Numeric(8, 6), nullable=False)
    source_revision: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("owner_id", "revision_number", name="uq_cortisol_pk_owner_revision"),
        CheckConstraint("revision_number > 0", name="cortisol_pk_revision_positive"),
        CheckConstraint(
            "elimination_half_life_hours BETWEEN 0.25 AND 12",
            name="cortisol_pk_half_life_bounded",
        ),
        CheckConstraint(
            "peak_time_hours BETWEEN 0.1 AND 8",
            name="cortisol_pk_peak_time_bounded",
        ),
        CheckConstraint(
            "distribution_volume_liters BETWEEN 1 AND 500",
            name="cortisol_pk_volume_bounded",
        ),
        CheckConstraint(
            "oral_bioavailability > 0 AND oral_bioavailability <= 1",
            name="cortisol_pk_bioavailability_bounded",
        ),
        Index(
            "ix_cortisol_pk_owner_revision_desc",
            "owner_id",
            revision_number.desc(),
        ),
        OPS_SCHEMA,
    )
