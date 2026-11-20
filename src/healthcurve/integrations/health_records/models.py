"""Imported health records: batches, identity envelopes, revisions, AI visibility.

ADR-0040. The external dataset is a full snapshot on every rebuild. HealthCurve keeps
one envelope per source ``record_id`` and appends a revision whenever the source row's
content changes, so a portal correction never overwrites what was imported before. A
record that disappears from a snapshot changes state; it is never deleted.

These tables are deliberately not granted to the analyst roles (ADR-0036). Chat and AI
reach imported data only through views that check ``health_record_ai_visibility``.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime
from enum import StrEnum
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import FACT_SCHEMA, OPS_SCHEMA, FactBase, OpsBase, StrEnumType


class DatasetTable(StrEnum):
    """Dataset tables HealthCurve imports. Values are the dataset's own table names."""

    LAB_RESULTS = "lab_results"
    IMAGING_STUDIES = "imaging_studies"
    DOCUMENTS = "documents"
    ENCOUNTERS = "encounters"
    MEDICATIONS = "medications"
    CONDITIONS = "conditions"
    IMMUNIZATIONS = "immunizations"
    ALLERGIES = "allergies"
    VITALS = "vitals"
    SURGICAL_HISTORY = "surgical_history"


class RecordState(StrEnum):
    PRESENT = "present"
    #: The latest imported snapshot no longer contains this ``record_id``.
    NO_LONGER_IN_SOURCE = "no_longer_in_source"


class AiVisibilityCategory(StrEnum):
    """Categories the owner can opt in to chat/AI. All default to hidden.

    Imported medication lists have no category on purpose: they can never enlarge the
    chat's medication vocabulary or be read as the plan or as doses (ADR-0040).
    """

    LABS = "labs"
    IMAGING_IMPRESSIONS = "imaging_impressions"
    IMAGING_REPORTS = "imaging_reports"
    ENCOUNTERS = "encounters"
    CONDITIONS = "conditions"
    IMMUNIZATIONS = "immunizations"
    ALLERGIES = "allergies"
    VITALS = "vitals"
    SURGICAL_HISTORY = "surgical_history"
    CLINICAL_NOTES = "clinical_notes"
    VISIT_SUMMARIES = "visit_summaries"
    LETTERS_AND_ADMIN = "letters_and_admin"
    OTHER_DOCUMENTS = "other_documents"


class HealthRecordImportBatch(FactBase):
    """One committed import run and the dataset provenance it read."""

    __tablename__ = "health_record_import_batch"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    dataset_schema_version: Mapped[int] = mapped_column(Integer, nullable=False)
    #: ``dataset_meta.generated_at`` exactly as written; the dataset stores no zone.
    dataset_generated_at: Mapped[str] = mapped_column(String(64), nullable=False)
    #: ``exported_at:<source>`` values keyed by source channel.
    source_exported_at: Mapped[dict[str, str]] = mapped_column(JSONB, nullable=False)
    snapshot_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: Per-table counts: new, changed, unchanged, no_longer_in_source, returned.
    counts: Mapped[dict[str, dict[str, int]]] = mapped_column(JSONB, nullable=False)

    __table_args__ = (
        CheckConstraint("char_length(snapshot_sha256) = 64", name="snapshot_sha256_length"),
        CheckConstraint("completed_at >= started_at", name="completed_after_started"),
        Index("ix_health_record_batch_owner_completed", "owner_id", "completed_at"),
        FACT_SCHEMA,
    )


class HealthRecord(FactBase):
    """Identity envelope for one dataset row, with its current display fields."""

    __tablename__ = "health_record"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="RESTRICT"), nullable=False
    )
    dataset_table: Mapped[DatasetTable] = mapped_column(
        StrEnumType(DatasetTable, 32), nullable=False
    )
    record_id: Mapped[str] = mapped_column(String(128), nullable=False)

    #: Channel it came through, kept for audit.
    source: Mapped[str] = mapped_column(String(200), nullable=False)
    #: Health system that produced it, shown to the owner as "from".
    organization: Mapped[str] = mapped_column(String(300), nullable=False)

    record_date: Mapped[date | None] = mapped_column(Date)
    #: The source's own date text, kept when it is free text (``sept '24``).
    date_text: Mapped[str | None] = mapped_column(String(100))
    #: Local wall-clock time and the zone it was resolved in, when the source has one.
    local_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=False))
    timezone: Mapped[str | None] = mapped_column(String(64))
    #: True when the zone came from the fallback, not an owner-confirmed organization.
    timezone_assumed: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    title: Mapped[str] = mapped_column(Text, nullable=False)
    category: Mapped[str | None] = mapped_column(String(120))
    kind: Mapped[str | None] = mapped_column(String(120))
    #: Null for medication lists, which can never be opted in.
    ai_category: Mapped[AiVisibilityCategory | None] = mapped_column(
        StrEnumType(AiVisibilityCategory, 32)
    )

    state: Mapped[RecordState] = mapped_column(
        StrEnumType(RecordState, 32), nullable=False, default=RecordState.PRESENT
    )
    first_seen_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fact.health_record_import_batch.id", ondelete="RESTRICT"), nullable=False
    )
    last_seen_batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fact.health_record_import_batch.id", ondelete="RESTRICT"), nullable=False
    )
    #: The batch whose snapshot first lacked this record; cleared if it returns.
    missing_since_batch_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("fact.health_record_import_batch.id", ondelete="RESTRICT")
    )
    current_revision: Mapped[int] = mapped_column(Integer, nullable=False, default=1)

    __table_args__ = (
        UniqueConstraint(
            "owner_id", "dataset_table", "record_id", name="uq_health_record_identity"
        ),
        CheckConstraint("current_revision >= 1", name="current_revision_positive"),
        CheckConstraint(
            "(state = 'present') = (missing_since_batch_id IS NULL)",
            name="missing_state_has_batch",
        ),
        CheckConstraint(
            "dataset_table <> 'medications' OR ai_category IS NULL",
            name="medications_never_ai_visible",
        ),
        Index("ix_health_record_owner_table_date", "owner_id", "dataset_table", "record_date"),
        FACT_SCHEMA,
    )


class HealthRecordRevision(FactBase):
    """Append-only content of one envelope at one point in the source's history."""

    __tablename__ = "health_record_revision"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="RESTRICT"), nullable=False
    )
    health_record_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fact.health_record.id", ondelete="CASCADE"), nullable=False
    )
    revision: Mapped[int] = mapped_column(Integer, nullable=False)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("fact.health_record_import_batch.id", ondelete="RESTRICT"), nullable=False
    )
    content_sha256: Mapped[str] = mapped_column(String(64), nullable=False)

    # Clinicians, free text as the source recorded them.
    provider: Mapped[str | None] = mapped_column(String(300))
    ordered_by: Mapped[str | None] = mapped_column(String(300))
    reading_provider: Mapped[str | None] = mapped_column(String(300))
    performing_lab: Mapped[str | None] = mapped_column(String(300))
    prescriber: Mapped[str | None] = mapped_column(String(300))

    #: Short text: an imaging impression, a medication sig, allergy reactions, a value.
    summary_text: Mapped[str | None] = mapped_column(Text)
    #: Long text: a document's extracted text or an imaging report.
    body_text: Mapped[str | None] = mapped_column(Text)
    #: Relative paths of the original files under the dataset root.
    file_paths: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    #: The complete source row minus the long text above, for audit.
    source_row: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        UniqueConstraint("health_record_id", "revision", name="uq_health_record_revision"),
        CheckConstraint("revision >= 1", name="revision_positive"),
        CheckConstraint("char_length(content_sha256) = 64", name="content_sha256_length"),
        Index("ix_health_record_revision_owner", "owner_id"),
        FACT_SCHEMA,
    )


class HealthRecordAiVisibility(OpsBase):
    """Owner opt-in for one category. A missing row means hidden."""

    __tablename__ = "health_record_ai_visibility"

    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), primary_key=True
    )
    category: Mapped[AiVisibilityCategory] = mapped_column(
        StrEnumType(AiVisibilityCategory, 32), primary_key=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (OPS_SCHEMA,)
