"""Immutable physician-report snapshots.

A report is an operational artifact that deliberately combines the safety partitions.
The partitions remain explicit inside every stored manifest and payload; rendering
never has to guess whether content is fact, plan, patient-authored, or AI-generated.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    String,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import OPS_SCHEMA, OpsBase


class ReportSnapshot(OpsBase):
    """Canonical report data frozen independently of its mutable source records."""

    __tablename__ = "report_snapshot"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    date_from: Mapped[date] = mapped_column(Date, nullable=False)
    date_to: Mapped[date] = mapped_column(Date, nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    selected_sections: Mapped[list[str]] = mapped_column(JSONB, nullable=False)
    include_ai: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false")
    )

    #: Explicit ``fact`` / ``plan`` / ``patient_note`` / ``ai`` source IDs.
    source_manifest: Mapped[dict[str, list[str]]] = mapped_column(JSONB, nullable=False)
    #: Deterministic values as rendered, including definition and timezone per metric.
    metric_values: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    #: Frozen content with the same four explicit partitions as the source manifest.
    snapshot_content: Mapped[dict[str, object]] = mapped_column(JSONB, nullable=False)
    render_version: Mapped[str] = mapped_column(String(32), nullable=False)
    canonical_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint("date_to >= date_from", name="report_date_ordered"),
        CheckConstraint("char_length(canonical_sha256) = 64", name="report_checksum_length"),
        Index("ix_report_snapshot_owner_created", "owner_id", "created_at"),
        OPS_SCHEMA,
    )
