"""Regimen versioning, approval, and plan-versus-actual comparison.

Two rules drive everything here:

* **Only a human approves a plan** (SAFE-16). :func:`approve_version` requires an
  approver and a source, and the database refuses an approved row without them.
* **A missed dose is an absence, not a zero** (SAFE-10). :func:`compare_day` derives
  missing slots by looking at what is *not* there. It never writes a row to represent
  a dose that was not taken.
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.orm import Session

from healthcurve.events import service as event_service
from healthcurve.medications.models import (
    DoseEvent,
    DoseUnit,
    Medication,
    RegimenDoseSlot,
    RegimenStatus,
    RegimenVersion,
    Route,
)

#: How far from its scheduled time a dose still counts as "on time". Documented rather
#: than implicit, and rendered beside every timing figure (SAFE-27).
ON_TIME_TOLERANCE: Final = timedelta(minutes=30)

TIMING_METRIC_DEFINITION: Final = (
    "A dose is matched to the nearest unmatched scheduled slot for the same medication "
    "within 4 hours. It is 'on time' within 30 minutes of the scheduled local time, "
    "'early' or 'late' otherwise. A slot with no matched dose is 'missing' -- derived "
    "from the absence of a record, never stored as a zero dose. A dose matching no slot "
    "is 'unplanned'. Days are bounded by local midnight in the stated timezone."
)

MATCH_WINDOW: Final = timedelta(hours=4)


class PlanError(Exception):
    """A regimen operation that would break the plan's invariants."""


def normalize_name(name: str) -> str:
    return " ".join(name.strip().lower().split())


# ---------------------------------------------------------------------------
# Regimen lifecycle
# ---------------------------------------------------------------------------


def create_draft(
    session: Session,
    *,
    owner_id: uuid.UUID,
    version_label: str,
    effective_from: datetime,
    effective_to: datetime | None = None,
    notes: str | None = None,
) -> RegimenVersion:
    """Create a draft. Drafts are freely editable; approved versions never are."""
    if effective_to is not None and effective_to <= effective_from:
        raise PlanError("effective_to must be after effective_from")

    version = RegimenVersion(
        owner_id=owner_id,
        version_label=version_label,
        status=RegimenStatus.DRAFT,
        effective_from=effective_from,
        effective_to=effective_to,
        effective_period=_period(effective_from, effective_to),
        notes=notes,
    )
    session.add(version)
    session.flush()
    return version


def update_draft(
    session: Session,
    version: RegimenVersion,
    *,
    version_label: str,
    effective_from: datetime,
    effective_to: datetime | None = None,
    notes: str | None = None,
) -> RegimenVersion:
    """Replace editable metadata on a draft; approved history is immutable."""
    if version.status is not RegimenStatus.DRAFT:
        raise PlanError("only an unapproved draft can be edited; create a new version")
    if effective_to is not None and effective_to <= effective_from:
        raise PlanError("effective_to must be after effective_from")

    version.version_label = version_label
    version.effective_from = effective_from
    version.effective_to = effective_to
    version.effective_period = _period(effective_from, effective_to)
    version.notes = notes
    return version


def _period(start: datetime, end: datetime | None) -> Range[datetime]:
    """The tsrange the exclusion constraint compares. Half-open, naive."""
    return Range(_naive(start), _naive(end) if end else None, bounds="[)")


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def approve_version(
    session: Session,
    version: RegimenVersion,
    *,
    approved_by: str,
    approval_source: str,
    approved_at: datetime | None = None,
    source_document_checksum: str | None = None,
) -> RegimenVersion:
    """Approve a draft. Only ever called from a human-initiated request (SAFE-16).

    Overlap with another approved version is refused by a database exclusion
    constraint, so this cannot create a period with two plans in force.
    """
    if version.status is RegimenStatus.RETIRED:
        raise PlanError("a retired version cannot be approved; create a new version")
    if version.status is RegimenStatus.APPROVED:
        raise PlanError("version is already approved; approved versions are immutable")
    if not approved_by.strip() or not approval_source.strip():
        raise PlanError("approval requires both an approver and a source")

    version.status = RegimenStatus.APPROVED
    version.approved_at = approved_at or datetime.now(UTC)
    version.approved_by = approved_by.strip()
    version.approval_source = approval_source.strip()
    version.source_document_checksum = source_document_checksum
    session.flush()
    return version


def retire_version(
    session: Session, version: RegimenVersion, *, retired_at: datetime | None = None
) -> RegimenVersion:
    """Retire a version. It stays queryable so past dates still resolve correctly."""
    if version.status is RegimenStatus.RETIRED:
        return version
    version.status = RegimenStatus.RETIRED
    version.retired_at = retired_at or datetime.now(UTC)
    if version.effective_to is None:
        version.effective_to = version.retired_at
        version.effective_period = _period(version.effective_from, version.effective_to)
    session.flush()
    return version


def active_version_at(
    session: Session, owner_id: uuid.UUID, moment: datetime
) -> RegimenVersion | None:
    """The approved version in force at ``moment``, or None.

    None is a real answer -- before the first approved plan there was no plan, and
    saying so is more honest than falling back to the newest one.
    """
    naive = _naive(moment)
    return session.scalar(
        select(RegimenVersion)
        .where(
            RegimenVersion.owner_id == owner_id,
            RegimenVersion.status == RegimenStatus.APPROVED,
            RegimenVersion.effective_from <= naive,
            (RegimenVersion.effective_to.is_(None)) | (RegimenVersion.effective_to > naive),
        )
        .order_by(RegimenVersion.effective_from.desc())
        .limit(1)
    )


def diff_versions(older: RegimenVersion, newer: RegimenVersion) -> dict[str, list[str]]:
    """A human-readable diff of two versions' dose slots."""

    def key(slot: RegimenDoseSlot) -> tuple[str, str]:
        return (slot.medication.name, slot.scheduled_local_time.isoformat())

    old_map = {key(s): s for s in older.slots}
    new_map = {key(s): s for s in newer.slots}

    added = [
        f"{s.medication.name} {s.amount} {s.unit} at {s.scheduled_local_time}"
        for k, s in new_map.items()
        if k not in old_map
    ]
    removed = [
        f"{s.medication.name} {s.amount} {s.unit} at {s.scheduled_local_time}"
        for k, s in old_map.items()
        if k not in new_map
    ]
    changed = [
        f"{k[0]} at {k[1]}: {old_map[k].amount} {old_map[k].unit} -> {s.amount} {s.unit}"
        for k, s in new_map.items()
        if k in old_map and old_map[k].amount != s.amount
    ]
    return {"added": added, "removed": removed, "changed": changed}


# ---------------------------------------------------------------------------
# Plan versus actual
# ---------------------------------------------------------------------------


class SlotComparison:
    """One row of the comparison. Plain object so the API layer owns serialisation."""

    __slots__ = (
        "actual_amount",
        "actual_local_time",
        "dose_id",
        "medication_id",
        "medication_name",
        "minutes_from_scheduled",
        "planned_amount",
        "route",
        "scheduled_local_time",
        "slot_id",
        "status",
        "unit",
    )

    def __init__(
        self,
        *,
        slot_id: uuid.UUID | None,
        medication_id: uuid.UUID,
        medication_name: str,
        scheduled_local_time: object | None,
        planned_amount: Decimal | None,
        actual_amount: Decimal | None,
        actual_local_time: datetime | None,
        dose_id: uuid.UUID | None,
        status: str,
        minutes_from_scheduled: int | None,
        unit: DoseUnit,
        route: Route,
    ) -> None:
        self.slot_id = slot_id
        self.medication_id = medication_id
        self.medication_name = medication_name
        self.scheduled_local_time = scheduled_local_time
        self.planned_amount = planned_amount
        self.actual_amount = actual_amount
        self.actual_local_time = actual_local_time
        self.dose_id = dose_id
        self.status = status
        self.minutes_from_scheduled = minutes_from_scheduled
        self.unit = unit
        self.route = route


def compare_day(
    session: Session,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
) -> dict[str, object]:
    """Compare one local day's doses against the plan in force that day.

    Day boundaries are local midnight in ``timezone``, so a dose at 23:50 belongs to
    the day the owner experienced it rather than to whichever UTC day it fell in.
    """
    from zoneinfo import ZoneInfo

    zone = ZoneInfo(timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
    end = start + timedelta(days=1)

    version = active_version_at(session, owner_id, start)

    doses = list(
        session.scalars(
            select(DoseEvent)
            .where(
                DoseEvent.owner_id == owner_id,
                DoseEvent.occurred_at >= start,
                DoseEvent.occurred_at < end,
            )
            .order_by(DoseEvent.occurred_at)
        )
    )
    # Corrections supersede: only the head of each chain counts toward totals.
    doses = event_service.current_only(session, DoseEvent, doses)

    slots = sorted(version.slots, key=lambda s: s.scheduled_local_time) if version else []

    comparisons: list[SlotComparison] = []
    unmatched = list(doses)

    for slot in slots:
        scheduled_local = datetime.combine(day, slot.scheduled_local_time)
        match = _best_match(slot, unmatched, scheduled_local)

        if match is None:
            comparisons.append(
                SlotComparison(
                    slot_id=slot.id,
                    medication_id=slot.medication_id,
                    medication_name=slot.medication.name,
                    scheduled_local_time=slot.scheduled_local_time,
                    planned_amount=slot.amount,
                    actual_amount=None,
                    actual_local_time=None,
                    dose_id=None,
                    status="missing",  # derived, never stored
                    minutes_from_scheduled=None,
                    unit=slot.unit,
                    route=slot.route,
                )
            )
            continue

        unmatched.remove(match)
        delta = match.local_time - scheduled_local
        minutes = int(delta.total_seconds() // 60)
        if abs(delta) <= ON_TIME_TOLERANCE:
            status = "on_time"
        elif delta > timedelta(0):
            status = "late"
        else:
            status = "early"

        comparisons.append(
            SlotComparison(
                slot_id=slot.id,
                medication_id=slot.medication_id,
                medication_name=slot.medication.name,
                scheduled_local_time=slot.scheduled_local_time,
                planned_amount=slot.amount,
                actual_amount=match.amount,
                actual_local_time=match.local_time,
                dose_id=match.id,
                status=status,
                minutes_from_scheduled=minutes,
                unit=match.unit,
                route=match.route,
            )
        )

    for dose in unmatched:
        comparisons.append(
            SlotComparison(
                slot_id=None,
                medication_id=dose.medication_id,
                medication_name=dose.medication.name,
                scheduled_local_time=None,
                planned_amount=None,
                actual_amount=dose.amount,
                actual_local_time=dose.local_time,
                dose_id=dose.id,
                status="unplanned",
                minutes_from_scheduled=None,
                unit=dose.unit,
                route=dose.route,
            )
        )

    planned_total = sum((s.amount for s in slots), Decimal(0)) if slots else None
    actual_total = sum((d.amount for d in doses), Decimal(0))

    return {
        "date": day,
        "timezone": timezone,
        "regimen_version_id": version.id if version else None,
        "regimen_version_label": version.version_label if version else None,
        "slots": comparisons,
        "planned_total": planned_total,
        "actual_total": actual_total,
        "unplanned_doses": sum(1 for c in comparisons if c.status == "unplanned"),
        "missed_slots": sum(1 for c in comparisons if c.status == "missing"),
        "metric_definition": TIMING_METRIC_DEFINITION,
    }


def _best_match(
    slot: RegimenDoseSlot, candidates: list[DoseEvent], scheduled_local: datetime
) -> DoseEvent | None:
    """The nearest unmatched dose of the same medication within the match window."""
    same_medication = [d for d in candidates if d.medication_id == slot.medication_id]
    if not same_medication:
        return None
    nearest = min(same_medication, key=lambda d: abs(d.local_time - scheduled_local))
    if abs(nearest.local_time - scheduled_local) > MATCH_WINDOW:
        return None
    return nearest


def find_medication_by_name(session: Session, owner_id: uuid.UUID, name: str) -> Medication | None:
    """Exact normalized-name lookup, used when resolving extraction output."""
    return session.scalar(
        select(Medication).where(
            Medication.owner_id == owner_id,
            Medication.normalized_name == normalize_name(name),
        )
    )
