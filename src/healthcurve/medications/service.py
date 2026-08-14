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
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from typing import Final
from zoneinfo import ZoneInfo

from sqlalchemy import and_, func, or_, select
from sqlalchemy.dialects.postgresql import Range
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from healthcurve.events import service as event_service
from healthcurve.events.timekeeping import EventTime, from_instant, resolve_event_time
from healthcurve.medications.models import (
    DoseCategory,
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
    "within 4 hours in the physician-approved plan's half-open historical effective "
    "interval. It is 'on time' within 30 minutes of the scheduled local time, 'early' "
    "or 'late' otherwise. Signed minutes are actual minus scheduled; absolute deviation "
    "uses only matched doses. A slot with no matched dose is 'missing' -- derived from "
    "the absence of a record, never stored as a zero dose. A dose matching no slot is "
    "'unplanned'; missing and unplanned rows are not treated as zero-minute deviations. "
    "Days are bounded by local midnight in the stated timezone."
)

MATCH_WINDOW: Final = timedelta(hours=4)


class PlanError(Exception):
    """A regimen operation that would break the plan's invariants."""


@dataclass(frozen=True)
class PlanActivation:
    """The approved version and any predecessor closed by the same transaction."""

    version: RegimenVersion
    predecessor: RegimenVersion | None


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
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    effective_timezone: str = "UTC",
    effective_from_fold: int | None = None,
    effective_to_fold: int | None = None,
    notes: str | None = None,
) -> RegimenVersion:
    """Create a draft. Drafts are freely editable; approved versions never are."""
    _validate_timezone(effective_timezone)
    resolved_from = (
        _resolve_plan_time(effective_from, effective_timezone, effective_from_fold)
        if effective_from is not None
        else None
    )
    resolved_to = (
        _resolve_plan_time(effective_to, effective_timezone, effective_to_fold)
        if effective_to is not None
        else None
    )
    normalized_from = _naive(resolved_from.occurred_at) if resolved_from is not None else None
    normalized_to = _naive(resolved_to.occurred_at) if resolved_to is not None else None
    if (
        normalized_from is not None
        and normalized_to is not None
        and normalized_to <= normalized_from
    ):
        raise PlanError("effective_to must be after effective_from")

    version = RegimenVersion(
        owner_id=owner_id,
        version_label=version_label,
        status=RegimenStatus.DRAFT,
        effective_from=normalized_from,
        effective_to=normalized_to,
        effective_period=_period(normalized_from, normalized_to),
        effective_timezone=effective_timezone,
        effective_from_local=resolved_from.local_time if resolved_from is not None else None,
        effective_to_local=resolved_to.local_time if resolved_to is not None else None,
        effective_from_utc_offset_minutes=(
            resolved_from.utc_offset_minutes if resolved_from is not None else None
        ),
        effective_to_utc_offset_minutes=(
            resolved_to.utc_offset_minutes if resolved_to is not None else None
        ),
        effective_time_provenance=(
            "explicit_timezone" if resolved_from is not None else "pending_activation"
        ),
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
    effective_from: datetime | None = None,
    effective_to: datetime | None = None,
    effective_timezone: str = "UTC",
    effective_from_fold: int | None = None,
    effective_to_fold: int | None = None,
    notes: str | None = None,
) -> RegimenVersion:
    """Replace editable metadata on a draft; approved history is immutable."""
    if version.status is not RegimenStatus.DRAFT:
        raise PlanError("only an unapproved draft can be edited; create a new version")
    _validate_timezone(effective_timezone)
    resolved_from = (
        _resolve_plan_time(effective_from, effective_timezone, effective_from_fold)
        if effective_from is not None
        else None
    )
    resolved_to = (
        _resolve_plan_time(effective_to, effective_timezone, effective_to_fold)
        if effective_to is not None
        else None
    )
    normalized_from = _naive(resolved_from.occurred_at) if resolved_from is not None else None
    normalized_to = _naive(resolved_to.occurred_at) if resolved_to is not None else None
    if (
        normalized_from is not None
        and normalized_to is not None
        and normalized_to <= normalized_from
    ):
        raise PlanError("effective_to must be after effective_from")

    version.version_label = version_label
    version.effective_from = normalized_from
    version.effective_to = normalized_to
    version.effective_period = _period(normalized_from, normalized_to)
    version.effective_timezone = effective_timezone
    version.effective_from_local = resolved_from.local_time if resolved_from is not None else None
    version.effective_to_local = resolved_to.local_time if resolved_to is not None else None
    version.effective_from_utc_offset_minutes = (
        resolved_from.utc_offset_minutes if resolved_from is not None else None
    )
    version.effective_to_utc_offset_minutes = (
        resolved_to.utc_offset_minutes if resolved_to is not None else None
    )
    version.effective_time_provenance = (
        "explicit_timezone" if resolved_from is not None else "pending_activation"
    )
    version.notes = notes
    return version


def _period(start: datetime | None, end: datetime | None) -> Range[datetime]:
    """The tsrange the exclusion constraint compares. Half-open, naive."""
    return Range(_naive(start) if start else None, _naive(end) if end else None, bounds="[)")


def _naive(value: datetime) -> datetime:
    return value.astimezone(UTC).replace(tzinfo=None) if value.tzinfo else value


def _resolve_plan_time(value: datetime, timezone: str, fold: int | None) -> EventTime:
    """Resolve plan input while retaining the owner's capture-time wall clock."""
    if value.tzinfo is not None:
        return from_instant(value, timezone)
    return resolve_event_time(value, timezone, fold=fold)


def _validate_timezone(timezone: str) -> None:
    try:
        ZoneInfo(timezone)
    except Exception as exc:
        raise PlanError(f"unknown IANA timezone: {timezone}") from exc


def approve_version(
    session: Session,
    version: RegimenVersion,
    *,
    approved_by: str,
    approval_source: str,
    approved_at: datetime | None = None,
    source_document_checksum: str | None = None,
    activation_at: datetime | None = None,
    activation_local_time: datetime | None = None,
    activation_timezone: str | None = None,
    activation_fold: int | None = None,
) -> RegimenVersion:
    """Approve a draft. Only ever called from a human-initiated request (SAFE-16).

    Overlap with another approved version is refused by a database exclusion
    constraint, so this cannot create a period with two plans in force.
    """
    return activate_version(
        session,
        version,
        approved_by=approved_by,
        approval_source=approval_source,
        approved_at=approved_at,
        source_document_checksum=source_document_checksum,
        activation_at=activation_at,
        activation_local_time=activation_local_time,
        activation_timezone=activation_timezone,
        activation_fold=activation_fold,
    ).version


def activate_version(
    session: Session,
    version: RegimenVersion,
    *,
    approved_by: str,
    approval_source: str,
    approved_at: datetime | None = None,
    source_document_checksum: str | None = None,
    activation_at: datetime | None = None,
    activation_local_time: datetime | None = None,
    activation_timezone: str | None = None,
    activation_fold: int | None = None,
) -> PlanActivation:
    """Approve a draft and atomically end its single approved predecessor."""
    if version.status is RegimenStatus.RETIRED:
        raise PlanError("a retired version cannot be approved; create a new version")
    if version.status is RegimenStatus.APPROVED:
        raise PlanError("version is already approved; approved versions are immutable")
    if not approved_by.strip() or not approval_source.strip():
        raise PlanError("approval requires both an approver and a source")

    activated_at = activation_at or datetime.now(UTC)
    if activated_at.tzinfo is None or activated_at.utcoffset() is None:
        raise PlanError("activation time must include a UTC offset")
    timezone = activation_timezone or version.effective_timezone or "UTC"
    _validate_timezone(timezone)
    if activation_local_time is not None:
        resolved_from = _resolve_plan_time(
            activation_local_time,
            timezone,
            activation_fold,
        )
        resolved_start = _naive(resolved_from.occurred_at)
        effective_time_provenance = "explicit_timezone"
    elif version.effective_from is None:
        resolved_from = from_instant(activated_at, timezone)
        resolved_start = _naive(resolved_from.occurred_at)
        effective_time_provenance = "activation_instant"
    else:
        resolved_from = None
        resolved_start = version.effective_from
        effective_time_provenance = version.effective_time_provenance
    if version.effective_to is not None and version.effective_to <= resolved_start:
        raise PlanError("effective_to must be after the resolved effective_from")

    # An owner-scoped transaction lock also serializes the no-predecessor case, where
    # there would otherwise be no row to lock. PostgreSQL's exclusion constraint
    # remains the final invariant guard.
    if session.get_bind().dialect.name == "postgresql":
        lock_key = int.from_bytes(version.owner_id.bytes[:8], byteorder="big", signed=True)
        session.execute(select(func.pg_advisory_xact_lock(lock_key)))

    versions = list(
        session.scalars(
            select(RegimenVersion)
            .where(
                RegimenVersion.owner_id == version.owner_id,
                RegimenVersion.id != version.id,
                _historically_approved_clause(),
            )
            .order_by(RegimenVersion.effective_from, RegimenVersion.id)
            .with_for_update()
        )
    )
    predecessors = [
        candidate
        for candidate in versions
        if candidate.effective_from is not None
        and candidate.effective_from < resolved_start
        and (candidate.effective_to is None or candidate.effective_to > resolved_start)
    ]
    if len(predecessors) > 1:
        raise PlanError("more than one approved predecessor overlaps the new start")

    predecessor = predecessors[0] if predecessors else None
    if predecessor is not None and predecessor.status is not RegimenStatus.APPROVED:
        raise PlanError(
            f"Plan “{predecessor.version_label}” already covers the requested start. "
            "Choose a start after that historical plan ends."
        )
    new_end = version.effective_to
    unsafe_overlaps = [
        candidate
        for candidate in versions
        if candidate is not predecessor
        and candidate.effective_from is not None
        and (new_end is None or candidate.effective_from < new_end)
        and (candidate.effective_to is None or candidate.effective_to > resolved_start)
    ]
    if unsafe_overlaps:
        conflict = unsafe_overlaps[0]
        conflict_effective_from = conflict.effective_from
        if conflict_effective_from is None:  # pragma: no cover - filtered above
            raise AssertionError("conflicting plan start unexpectedly missing")
        conflict_start = conflict_effective_from.strftime("%b %-d, %Y at %H:%M")
        raise PlanError(
            f"Plan “{conflict.version_label}” starts {conflict_start} and conflicts with "
            "this plan's requested dates. Choose a start after that plan ends, or shorten "
            "this plan's optional end date."
        )

    # Only mutate after every check succeeds, so callers that catch PlanError cannot
    # accidentally commit a partially resolved draft or shortened predecessor.
    if resolved_from is not None:
        version.effective_from = resolved_start
        version.effective_from_local = resolved_from.local_time
        version.effective_from_utc_offset_minutes = resolved_from.utc_offset_minutes
        version.effective_timezone = timezone
        version.effective_time_provenance = effective_time_provenance
        version.effective_period = _period(resolved_start, version.effective_to)

    if predecessor is not None:
        predecessor.effective_to = resolved_start
        predecessor.effective_period = _period(predecessor.effective_from, predecessor.effective_to)
        predecessor_timezone = predecessor.effective_timezone or timezone
        predecessor_end = from_instant(resolved_start.replace(tzinfo=UTC), predecessor_timezone)
        predecessor.effective_to_local = predecessor_end.local_time
        predecessor.effective_to_utc_offset_minutes = predecessor_end.utc_offset_minutes
        # PostgreSQL checks the exclusion constraint after each statement. Flush the
        # shortened predecessor before approving the successor so the two effective
        # periods never overlap, even transiently within this transaction.
        session.flush([predecessor])

    version.status = RegimenStatus.APPROVED
    version.approved_at = approved_at or datetime.now(UTC)
    version.approved_by = approved_by.strip()
    version.approval_source = approval_source.strip()
    version.source_document_checksum = source_document_checksum
    session.flush()
    return PlanActivation(version=version, predecessor=predecessor)


def retire_version(
    session: Session, version: RegimenVersion, *, retired_at: datetime | None = None
) -> RegimenVersion:
    """Retire a version. It stays queryable so past dates still resolve correctly."""
    if version.status is RegimenStatus.RETIRED:
        return version
    version.status = RegimenStatus.RETIRED
    retired_moment = retired_at or datetime.now(UTC)
    version.retired_at = retired_moment
    retired_end = _naive(retired_moment)
    version.effective_from = (
        _naive(version.effective_from) if version.effective_from is not None else None
    )
    current_end = _naive(version.effective_to) if version.effective_to is not None else None
    version.effective_to = current_end
    if current_end is None or current_end > retired_end:
        version.effective_to = retired_end
        if version.effective_timezone is not None:
            resolved_end = from_instant(retired_moment, version.effective_timezone)
            version.effective_to_local = resolved_end.local_time
            version.effective_to_utc_offset_minutes = resolved_end.utc_offset_minutes
        else:
            # A legacy row's original zone is unknown. Preserve that disclosure
            # instead of making the retirement instant look like recovered context.
            version.effective_to_local = None
            version.effective_to_utc_offset_minutes = None
    version.effective_period = _period(version.effective_from, version.effective_to)
    session.flush()
    return version


def active_version_at(
    session: Session, owner_id: uuid.UUID, moment: datetime
) -> RegimenVersion | None:
    """The historically approved version in force at ``moment``, or None.

    None is a real answer -- before the first approved plan there was no plan, and
    saying so is more honest than falling back to the newest one. Retiring a version
    closes its effective interval; it does not erase the plan's earlier history.
    """
    naive = _naive(moment)
    return session.scalar(
        select(RegimenVersion)
        .where(
            RegimenVersion.owner_id == owner_id,
            _historically_approved_clause(),
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
        "absolute_minutes_from_scheduled",
        "actual_amount",
        "actual_local_time",
        "dose_id",
        "medication_id",
        "medication_name",
        "minutes_from_scheduled",
        "planned_amount",
        "regimen_effective_from",
        "regimen_effective_to",
        "regimen_version_id",
        "regimen_version_label",
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
        regimen_version: RegimenVersion | None = None,
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
        self.absolute_minutes_from_scheduled = (
            abs(minutes_from_scheduled) if minutes_from_scheduled is not None else None
        )
        self.regimen_version_id = regimen_version.id if regimen_version else None
        self.regimen_version_label = regimen_version.version_label if regimen_version else None
        self.regimen_effective_from = regimen_version.effective_from if regimen_version else None
        self.regimen_effective_to = regimen_version.effective_to if regimen_version else None
        self.unit = unit
        self.route = route


def compare_day(
    session: Session,
    *,
    owner_id: uuid.UUID,
    day: date,
    timezone: str,
) -> dict[str, object]:
    """Compare one local day's doses against the plans historically in force that day.

    Day boundaries are local midnight in ``timezone``, so a dose at 23:50 belongs to
    the day the owner experienced it rather than to whichever UTC day it fell in.
    """
    zone = ZoneInfo(timezone)
    start = datetime.combine(day, datetime.min.time(), tzinfo=zone)
    end = start + timedelta(days=1)

    versions = _historical_versions_overlapping(session, owner_id, start, end)
    day_versions = _versions_active_during(versions, start, end)

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

    scheduled_slots: list[tuple[RegimenVersion, RegimenDoseSlot, datetime]] = []
    for version in versions:
        for slot in version.slots:
            scheduled_local = datetime.combine(day, slot.scheduled_local_time)
            winning_version = _active_from_candidates(
                versions, scheduled_local.replace(tzinfo=zone)
            )
            if winning_version is not None and winning_version.id == version.id:
                scheduled_slots.append((version, slot, scheduled_local))
    scheduled_slots.sort(key=lambda item: (item[2], item[1].sort_order, item[1].id))

    comparisons: list[SlotComparison] = []
    unmatched = list(doses)
    # A stress dose is an explicitly separate recorded fact. It must never silently
    # satisfy a regular physician-plan slot merely because its time is nearby.
    regular_doses = [dose for dose in doses if dose.category is not DoseCategory.STRESS]
    matches = _match_scheduled_slots(scheduled_slots, regular_doses, zone)

    for version, slot, scheduled_local in scheduled_slots:
        match = matches.get(slot.id)

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
                    regimen_version=version,
                    unit=slot.unit,
                    route=slot.route,
                )
            )
            continue

        unmatched.remove(match)
        actual_local = _local_in_zone(match, zone)
        delta = actual_local - scheduled_local
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
                actual_local_time=actual_local,
                dose_id=match.id,
                status=status,
                minutes_from_scheduled=minutes,
                regimen_version=version,
                unit=match.unit,
                route=match.route,
            )
        )

    for dose in unmatched:
        dose_version = active_version_at(session, owner_id, dose.occurred_at)
        comparisons.append(
            SlotComparison(
                slot_id=None,
                medication_id=dose.medication_id,
                medication_name=dose.medication.name,
                scheduled_local_time=None,
                planned_amount=None,
                actual_amount=dose.amount,
                actual_local_time=_local_in_zone(dose, zone),
                dose_id=dose.id,
                status="unplanned",
                minutes_from_scheduled=None,
                regimen_version=dose_version,
                unit=dose.unit,
                route=dose.route,
            )
        )

    planned_total = (
        sum((slot.amount for _, slot, _ in scheduled_slots), Decimal(0))
        if scheduled_slots
        else None
    )
    actual_total = sum((d.amount for d in doses), Decimal(0))

    return {
        "date": day,
        "timezone": timezone,
        "regimen_version_id": day_versions[0].id if len(day_versions) == 1 else None,
        "regimen_version_label": (
            day_versions[0].version_label if len(day_versions) == 1 else None
        ),
        "regimen_versions": day_versions,
        "slots": comparisons,
        "planned_total": planned_total,
        "actual_total": actual_total,
        "unplanned_doses": sum(1 for c in comparisons if c.status == "unplanned"),
        "missed_slots": sum(1 for c in comparisons if c.status == "missing"),
        "metric_definition": TIMING_METRIC_DEFINITION,
    }


def _match_scheduled_slots(
    scheduled_slots: list[tuple[RegimenVersion, RegimenDoseSlot, datetime]],
    doses: list[DoseEvent],
    zone: ZoneInfo,
) -> dict[uuid.UUID, DoseEvent]:
    """Choose deterministic one-to-one matches by smallest local-time distance."""
    candidates: list[tuple[timedelta, datetime, datetime, uuid.UUID, uuid.UUID, DoseEvent]] = []
    for _, slot, scheduled_local in scheduled_slots:
        for dose in doses:
            if dose.medication_id != slot.medication_id:
                continue
            actual_local = _local_in_zone(dose, zone)
            distance = abs(actual_local - scheduled_local)
            if distance <= MATCH_WINDOW:
                candidates.append((distance, scheduled_local, actual_local, slot.id, dose.id, dose))
    candidates.sort(key=lambda item: item[:5])

    matched_slots: set[uuid.UUID] = set()
    matched_doses: set[uuid.UUID] = set()
    matches: dict[uuid.UUID, DoseEvent] = {}
    for _, _, _, slot_id, dose_id, dose in candidates:
        if slot_id in matched_slots or dose_id in matched_doses:
            continue
        matches[slot_id] = dose
        matched_slots.add(slot_id)
        matched_doses.add(dose_id)
    return matches


def _local_in_zone(dose: DoseEvent, zone: ZoneInfo) -> datetime:
    """Return a dose's wall time in the selected comparison timezone."""
    return dose.occurred_at.astimezone(zone).replace(tzinfo=None)


def _historical_versions_overlapping(
    session: Session,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[RegimenVersion]:
    """Approved history whose half-open interval intersects ``[start, end)``."""
    naive_start = _naive(start)
    naive_end = _naive(end)
    return list(
        session.scalars(
            select(RegimenVersion)
            .where(
                RegimenVersion.owner_id == owner_id,
                _historically_approved_clause(),
                RegimenVersion.effective_from < naive_end,
                (RegimenVersion.effective_to.is_(None))
                | (RegimenVersion.effective_to > naive_start),
            )
            .order_by(RegimenVersion.effective_from)
        )
    )


def _active_from_candidates(
    versions: list[RegimenVersion], moment: datetime
) -> RegimenVersion | None:
    """Resolve a winner from loaded history using the same interval rules."""
    naive = _naive(moment)
    eligible = [
        version
        for version in versions
        if _was_approved(version)
        and version.effective_from is not None
        and version.effective_from <= naive
        and (version.effective_to is None or version.effective_to > naive)
    ]
    return (
        max(
            eligible,
            key=lambda version: (
                version.effective_from or datetime.min.replace(tzinfo=UTC).replace(tzinfo=None)
            ),
        )
        if eligible
        else None
    )


def _historically_approved_clause() -> ColumnElement[bool]:
    """SQL predicate excluding drafts that were merely moved to retired status."""
    return or_(
        RegimenVersion.status == RegimenStatus.APPROVED,
        and_(
            RegimenVersion.status == RegimenStatus.RETIRED,
            RegimenVersion.approved_at.is_not(None),
            RegimenVersion.approved_by.is_not(None),
        ),
    )


def _was_approved(version: RegimenVersion) -> bool:
    return version.status is RegimenStatus.APPROVED or (
        version.status is RegimenStatus.RETIRED
        and version.approved_at is not None
        and version.approved_by is not None
    )


def _versions_active_during(
    versions: list[RegimenVersion], start: datetime, end: datetime
) -> list[RegimenVersion]:
    """Return each winning plan version for at least one instant in the day."""
    probes = [start]
    naive_start = _naive(start)
    naive_end = _naive(end)
    probes.extend(
        version.effective_from.replace(tzinfo=UTC)
        for version in versions
        if version.effective_from is not None and naive_start <= version.effective_from < naive_end
    )
    active: list[RegimenVersion] = []
    seen: set[uuid.UUID] = set()
    for probe in sorted(probes):
        version = _active_from_candidates(versions, probe)
        if version is not None and version.id not in seen:
            active.append(version)
            seen.add(version.id)
    return active


def approved_versions_during(
    session: Session,
    *,
    owner_id: uuid.UUID,
    start: datetime,
    end: datetime,
) -> list[RegimenVersion]:
    """Return physician-approved plan versions active during a half-open UTC window."""
    return _versions_active_during(
        _historical_versions_overlapping(session, owner_id, start, end), start, end
    )


def association_for_event_time(
    session: Session,
    *,
    owner_id: uuid.UUID,
    medication_id: uuid.UUID,
    occurred_at: datetime,
    local_time: datetime,
    timezone: str,
) -> tuple[RegimenVersion | None, RegimenDoseSlot | None]:
    """Resolve the plan and nearest valid slot for a newly timed dose fact.

    A time correction must not carry an association from the original instant into a
    different historical plan period.
    """
    version = active_version_at(session, owner_id, occurred_at)
    if version is None:
        return None, None
    zone = ZoneInfo(timezone)
    candidates: list[tuple[timedelta, RegimenDoseSlot]] = []
    for slot in version.slots:
        if slot.medication_id != medication_id:
            continue
        scheduled_local = datetime.combine(local_time.date(), slot.scheduled_local_time)
        scheduled_version = active_version_at(
            session, owner_id, scheduled_local.replace(tzinfo=zone)
        )
        if scheduled_version is None or scheduled_version.id != version.id:
            continue
        delta = abs(local_time - scheduled_local)
        if delta <= MATCH_WINDOW:
            candidates.append((delta, slot))
    if not candidates:
        return version, None
    candidates.sort(key=lambda item: (item[0], item[1].sort_order, item[1].id))
    return version, candidates[0][1]


def find_medication_by_name(session: Session, owner_id: uuid.UUID, name: str) -> Medication | None:
    """Exact normalized-name lookup, used when resolving extraction output."""
    return session.scalar(
        select(Medication).where(
            Medication.owner_id == owner_id,
            Medication.normalized_name == normalize_name(name),
        )
    )
