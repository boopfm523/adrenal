"""Laboratory facts with source strings separate from derived normalization."""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    SmallInteger,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from healthcurve.db import FACT_SCHEMA, FactBase
from healthcurve.events.base import EventMixin, event_table_args


class LabPanel(EventMixin, FactBase):
    """One laboratory report; EventMixin time is the specimen collection time."""

    __tablename__ = "lab_panel"

    laboratory_name: Mapped[str | None] = mapped_column(String(300))
    accession_id: Mapped[str | None] = mapped_column(String(255))
    specimen_type: Mapped[str | None] = mapped_column(String(255))
    report_status: Mapped[str | None] = mapped_column(String(120))

    reported_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    reported_local_time: Mapped[datetime] = mapped_column(DateTime(timezone=False), nullable=False)
    reported_timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    reported_utc_offset_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)

    results: Mapped[list[LabResult]] = relationship(
        back_populates="panel", cascade="all, delete-orphan"
    )

    __table_args__ = (
        UniqueConstraint("id", "owner_id", name="uq_lab_panel_id_owner"),
        CheckConstraint("reported_at >= occurred_at", name="report_after_specimen"),
        CheckConstraint(
            "reported_utc_offset_minutes BETWEEN -720 AND 840",
            name="report_offset_within_real_range",
        ),
        *event_table_args("lab_panel"),
    )


class LabResult(FactBase):
    """One analyte/result exactly as printed, plus optional derived normalization."""

    __tablename__ = "lab_result"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    panel_id: Mapped[uuid.UUID] = mapped_column(nullable=False, index=True)
    source_row_index: Mapped[int | None] = mapped_column(Integer)

    analyte_name: Mapped[str] = mapped_column(Text, nullable=False)
    original_value: Mapped[str | None] = mapped_column(Text)
    qualitative_result: Mapped[str | None] = mapped_column(Text)
    original_unit: Mapped[str | None] = mapped_column(Text)
    original_reference_range: Mapped[str | None] = mapped_column(Text)
    #: Provider text such as H/L/abnormal. HealthCurve never computes this from a range.
    abnormal_flag: Mapped[str | None] = mapped_column(Text)

    normalized_analyte_code: Mapped[str | None] = mapped_column(String(120))
    normalized_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 10))
    normalized_unit: Mapped[str | None] = mapped_column(String(64))
    normalization_method: Mapped[str | None] = mapped_column(String(120))

    panel: Mapped[LabPanel] = relationship(back_populates="results")

    __table_args__ = (
        ForeignKeyConstraint(
            ["panel_id", "owner_id"],
            ["fact.lab_panel.id", "fact.lab_panel.owner_id"],
            ondelete="CASCADE",
        ),
        CheckConstraint(
            "original_value IS NOT NULL OR qualitative_result IS NOT NULL",
            name="lab_result_has_source_value",
        ),
        CheckConstraint(
            "normalized_value IS NULL OR "
            "(normalized_unit IS NOT NULL AND normalization_method IS NOT NULL)",
            name="normalized_value_has_provenance",
        ),
        CheckConstraint(
            "source_row_index IS NULL OR source_row_index >= 0",
            name="source_row_nonnegative",
        ),
        Index("ix_lab_result_analyte", "owner_id", "analyte_name"),
        FACT_SCHEMA,
    )
