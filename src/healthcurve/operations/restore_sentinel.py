"""Deterministic, non-health canary used only by isolated restore drills."""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from decimal import Decimal
from typing import Final

from sqlalchemy import CheckConstraint, DateTime, Integer, Numeric, SmallInteger, String, Uuid, func
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import OPS_SCHEMA, OpsBase

RESTORE_SENTINEL_ID: Final = uuid.UUID("7de965ba-2fed-4a17-9f85-b0d984f87849")
RESTORE_SENTINEL_VERSION: Final = 1
RESTORE_SENTINEL_ORIGINAL: Final = Decimal("17.031250")
RESTORE_SENTINEL_CORRECTED: Final = Decimal("19.062500")
RESTORE_SENTINEL_SOURCE: Final = "synthetic_restore_drill"
RESTORE_SENTINEL_CORRECTION_SOURCE: Final = "synthetic_owner_correction"
RESTORE_SENTINEL_OCCURRED_AT: Final = datetime(2024, 2, 29, 17, 34, 56, 789000, tzinfo=UTC)
RESTORE_SENTINEL_TIMEZONE: Final = "America/New_York"
RESTORE_SENTINEL_UTC_OFFSET_MINUTES: Final = -300


class RestoreSentinel(OpsBase):
    """A fixed row proving exact database semantics survive backup and restore.

    This is operational test data, not a recorded health fact and not product-visible.
    """

    __tablename__ = "restore_sentinel"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True)
    marker_version: Mapped[int] = mapped_column(Integer, nullable=False, unique=True)
    original_decimal: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    corrected_decimal: Mapped[Decimal] = mapped_column(Numeric(18, 6), nullable=False)
    source: Mapped[str] = mapped_column(String(64), nullable=False)
    correction_source: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    utc_offset_minutes: Mapped[int] = mapped_column(SmallInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )

    __table_args__ = (
        CheckConstraint(
            "corrected_decimal <> original_decimal",
            name="restore_sentinel_correction_changes_value",
        ),
        CheckConstraint(
            "utc_offset_minutes BETWEEN -840 AND 840",
            name="restore_sentinel_offset_bounded",
        ),
        OPS_SCHEMA,
    )


def expected_restore_sentinel() -> tuple[object, ...]:
    """Return the exact values the drill verifies, without logging them."""

    return (
        RESTORE_SENTINEL_VERSION,
        RESTORE_SENTINEL_ORIGINAL,
        RESTORE_SENTINEL_CORRECTED,
        RESTORE_SENTINEL_SOURCE,
        RESTORE_SENTINEL_CORRECTION_SOURCE,
        RESTORE_SENTINEL_OCCURRED_AT,
        RESTORE_SENTINEL_TIMEZONE,
        RESTORE_SENTINEL_UTC_OFFSET_MINUTES,
    )
