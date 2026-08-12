"""Durable request and artifact metadata for complete private exports."""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import OPS_SCHEMA, OpsBase


class PrivateExport(OpsBase):
    """One owner request, its durable job, progress, and immutable artifact."""

    __tablename__ = "private_export"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    owner_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("identity.owner.id", ondelete="CASCADE"), nullable=False, index=True
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ops.job.id", ondelete="CASCADE"), nullable=False, unique=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), nullable=False)
    request_fingerprint_sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    include_ai: Mapped[bool] = mapped_column(Boolean, nullable=False, server_default=text("false"))
    include_sensitive: Mapped[bool] = mapped_column(
        Boolean, nullable=False, server_default=text("true")
    )

    total_rows: Mapped[int | None] = mapped_column(Integer)
    processed_rows: Mapped[int] = mapped_column(Integer, nullable=False, server_default=text("0"))

    relative_path: Mapped[str | None] = mapped_column(String(500))
    sha256: Mapped[str | None] = mapped_column(String(64))
    byte_size: Mapped[int | None] = mapped_column(BigInteger)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    purged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    __table_args__ = (
        UniqueConstraint("owner_id", "idempotency_key", name="uq_private_export_owner_key"),
        CheckConstraint(
            "char_length(request_fingerprint_sha256) = 64", name="request_fingerprint_length"
        ),
        CheckConstraint("processed_rows >= 0", name="processed_rows_nonnegative"),
        CheckConstraint("total_rows IS NULL OR total_rows >= 0", name="total_rows_nonnegative"),
        CheckConstraint(
            "total_rows IS NULL OR processed_rows <= total_rows", name="progress_within_total"
        ),
        CheckConstraint(
            "(relative_path IS NULL AND sha256 IS NULL AND byte_size IS NULL) OR "
            "(relative_path IS NOT NULL AND sha256 IS NOT NULL AND byte_size > 0)",
            name="artifact_metadata_complete",
        ),
        CheckConstraint("sha256 IS NULL OR char_length(sha256) = 64", name="artifact_hash_length"),
        CheckConstraint("expires_at > created_at", name="expiry_after_creation"),
        Index("ix_private_export_owner_created", "owner_id", "created_at"),
        Index("ix_private_export_expiry", "expires_at", "purged_at"),
        OPS_SCHEMA,
    )
