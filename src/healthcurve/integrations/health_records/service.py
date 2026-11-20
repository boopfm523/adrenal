"""Idempotent import of a dataset snapshot into HealthCurve (ADR-0040).

One call is one import batch, in the caller's transaction:

1. Diff every dataset row against its envelope by ``record_id``. New rows get an
   envelope and revision 1; rows whose content hash changed get a new revision; rows
   missing from the snapshot become ``no_longer_in_source``; nothing is deleted.
2. Rebuild lab panels from the current lab revisions. A panel whose content changed is
   corrected through the existing ``supersedes_id`` chain, so earlier values stay in
   history and the Labs page shows one current version.

``dry_run`` performs the same diff and returns the counts without writing.
"""

from __future__ import annotations

import hashlib
import json
import math
import uuid
from collections import defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time
from typing import Any, Final

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.timekeeping import (
    EventTime,
    NonExistentLocalTimeError,
    load_zone,
    resolve_event_time,
)
from healthcurve.integrations.health_records.models import (
    AiVisibilityCategory,
    DatasetTable,
    HealthRecord,
    HealthRecordImportBatch,
    HealthRecordRevision,
    RecordState,
)
from healthcurve.integrations.health_records.reader import DatasetSnapshot
from healthcurve.labs.imports import LabCandidate
from healthcurve.labs.models import LabPanel
from healthcurve.labs.service import create_panel
from healthcurve.operations import audit

EASTERN: Final = "America/New_York"
CENTRAL: Final = "America/Chicago"
#: Owner-confirmed zone per producing organization (ADR-0040 section 4). Matched
#: case-insensitively on a distinctive substring of the organization name.
ORGANIZATION_TIMEZONES: Final[tuple[tuple[str, str], ...]] = (
    ("mayo clinic", CENTRAL),
    ("nih clinical center", EASTERN),
    ("johns hopkins", EASTERN),
    ("umms", EASTERN),
    ("university of maryland", EASTERN),
)
#: Any other organization (Inbox uploads, unknown): assumed, and flagged as assumed.
FALLBACK_TIMEZONE: Final = EASTERN
#: A date without a time is placed at local noon so it cannot shift calendar day.
DATE_ONLY_TIME: Final = time(12, 0)

LAB_PANEL_PROVIDER_PREFIX: Final = "health-records:lab-panel:"
LAB_CORRECTION_REASON: Final = "source dataset revised this panel"

#: Long text kept in typed revision columns rather than duplicated in ``source_row``.
_LONG_TEXT_COLUMNS: Final = frozenset({"text", "report_text", "impression"})

#: Document folders and the opt-in category each falls under. Anything unlisted,
#: including health summaries that may hold family history, is ``other_documents``.
_DOCUMENT_CATEGORIES: Final[Mapping[str, AiVisibilityCategory]] = {
    "clinical-notes": AiVisibilityCategory.CLINICAL_NOTES,
    "hospital-stays-discharge": AiVisibilityCategory.CLINICAL_NOTES,
    "surgery-procedures": AiVisibilityCategory.CLINICAL_NOTES,
    "visit-summaries": AiVisibilityCategory.VISIT_SUMMARIES,
    "letters": AiVisibilityCategory.LETTERS_AND_ADMIN,
    "consents-and-admin": AiVisibilityCategory.LETTERS_AND_ADMIN,
    "patient-education": AiVisibilityCategory.LETTERS_AND_ADMIN,
    "radiology": AiVisibilityCategory.IMAGING_REPORTS,
}

_TABLE_CATEGORIES: Final[Mapping[DatasetTable, AiVisibilityCategory | None]] = {
    DatasetTable.LAB_RESULTS: AiVisibilityCategory.LABS,
    DatasetTable.IMAGING_STUDIES: AiVisibilityCategory.IMAGING_IMPRESSIONS,
    DatasetTable.ENCOUNTERS: AiVisibilityCategory.ENCOUNTERS,
    # Never opt-in-able: medication lists are reference history only.
    DatasetTable.MEDICATIONS: None,
    DatasetTable.CONDITIONS: AiVisibilityCategory.CONDITIONS,
    DatasetTable.IMMUNIZATIONS: AiVisibilityCategory.IMMUNIZATIONS,
    DatasetTable.ALLERGIES: AiVisibilityCategory.ALLERGIES,
    DatasetTable.VITALS: AiVisibilityCategory.VITALS,
    DatasetTable.SURGICAL_HISTORY: AiVisibilityCategory.SURGICAL_HISTORY,
}


@dataclass
class TableCounts:
    new: int = 0
    changed: int = 0
    unchanged: int = 0
    no_longer_in_source: int = 0
    returned: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "new": self.new,
            "changed": self.changed,
            "unchanged": self.unchanged,
            "no_longer_in_source": self.no_longer_in_source,
            "returned": self.returned,
        }


@dataclass
class LabCounts:
    panels_created: int = 0
    panels_corrected: int = 0
    panels_unchanged: int = 0
    #: Source rows with neither a value nor a text result; kept as records only.
    results_without_value: int = 0

    def as_dict(self) -> dict[str, int]:
        return {
            "panels_created": self.panels_created,
            "panels_corrected": self.panels_corrected,
            "panels_unchanged": self.panels_unchanged,
            "results_without_value": self.results_without_value,
        }


@dataclass
class ImportResult:
    dry_run: bool
    batch_id: uuid.UUID | None
    tables: dict[DatasetTable, TableCounts] = field(default_factory=dict)
    labs: LabCounts = field(default_factory=LabCounts)

    def counts(self) -> dict[str, dict[str, int]]:
        payload = {table.value: counts.as_dict() for table, counts in self.tables.items()}
        payload["lab_panels"] = self.labs.as_dict()
        return payload


@dataclass(frozen=True)
class Projection:
    """How one dataset row appears in HealthCurve, independent of its revision history."""

    title: str
    category: str | None
    kind: str | None
    record_date: date | None
    date_text: str | None
    local_time: datetime | None
    timezone: str | None
    timezone_assumed: bool
    ai_category: AiVisibilityCategory | None
    provider: str | None = None
    ordered_by: str | None = None
    reading_provider: str | None = None
    performing_lab: str | None = None
    prescriber: str | None = None
    summary_text: str | None = None
    body_text: str | None = None
    file_paths: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def timezone_for(organization: str) -> tuple[str, bool]:
    """The zone for an organization's wall-clock times, and whether it was assumed."""
    folded = organization.casefold()
    for needle, zone in ORGANIZATION_TIMEZONES:
        if needle in folded:
            return zone, False
    return FALLBACK_TIMEZONE, True


def resolve_wall_time(local: datetime, zone: str) -> EventTime:
    """Resolve a portal wall time without asking: the portal gave no fold.

    A repeated fall-back hour takes its first occurrence. A skipped spring-forward time
    keeps its wall-clock reading with the pre-transition offset.
    """
    try:
        return resolve_event_time(local, zone, fold=0)
    except NonExistentLocalTimeError:
        aware = local.replace(tzinfo=load_zone(zone), fold=0)
        offset = aware.utcoffset()
        assert offset is not None
        return EventTime(
            occurred_at=aware.astimezone(UTC),
            local_time=local,
            timezone=zone,
            utc_offset_minutes=int(offset.total_seconds() // 60),
        )


def content_hash(row: Mapping[str, Any]) -> str:
    canonical = json.dumps(_jsonable_row(row), sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(canonical.encode()).hexdigest()


def _jsonable_row(row: Mapping[str, Any]) -> dict[str, Any]:
    return {str(key): _jsonable(value) for key, value in row.items()}


def _jsonable(value: Any) -> Any:
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, bytes):
        return {"encoding": "hex", "data": value.hex()}
    return value


def _text(row: Mapping[str, Any], key: str) -> str | None:
    value = row.get(key)
    if value is None:
        return None
    rendered = str(value).strip()
    return rendered or None


def _clip(value: str | None, limit: int) -> str | None:
    if value is None or len(value) <= limit:
        return value
    return value[: limit - 1] + "…"


def _parse_date(value: str | None) -> date | None:
    if value is None:
        return None
    try:
        return date.fromisoformat(value[:10]) if len(value) >= 10 else None
    except ValueError:
        return None


def _parse_local_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    try:
        return datetime.strptime(value[:16], "%Y-%m-%d %H:%M")  # noqa: DTZ007 -- wall clock
    except ValueError:
        return None


def _parse_clock(value: str | None) -> time | None:
    if value is None:
        return None
    for pattern in ("%H:%M", "%I:%M %p", "%H:%M:%S"):
        try:
            return datetime.strptime(value.strip().upper(), pattern).time()  # noqa: DTZ007
        except ValueError:
            continue
    return None


def _files(*values: str | None) -> tuple[str, ...]:
    paths: list[str] = []
    for value in values:
        if value is None:
            continue
        for part in value.split(";"):
            cleaned = part.strip()
            if cleaned and cleaned not in paths:
                paths.append(cleaned)
    return tuple(paths)


def _value_with_unit(row: Mapping[str, Any]) -> str | None:
    value = _text(row, "value_text")
    if value is None and row.get("value_num") is not None:
        value = str(row["value_num"])
    units = _text(row, "units")
    if value is None:
        return None
    return f"{value} {units}" if units else value


def project(table: DatasetTable, row: Mapping[str, Any]) -> Projection:
    """Map one dataset row to its display, provenance, and opt-in category."""
    organization = _text(row, "organization") or "Unknown organization"
    zone, assumed = timezone_for(organization)
    record_date = _parse_date(_text(row, "date"))
    local: datetime | None = None
    common: dict[str, Any] = {"timezone": zone, "timezone_assumed": assumed}
    match table:
        case DatasetTable.LAB_RESULTS:
            local = _parse_local_datetime(_text(row, "datetime"))
            return Projection(
                title=_text(row, "test") or _text(row, "test_normalized") or "Unnamed test",
                category=_text(row, "panel"),
                kind=_text(row, "test_normalized"),
                record_date=record_date,
                date_text=None,
                local_time=local,
                ai_category=AiVisibilityCategory.LABS,
                ordered_by=_text(row, "ordered_by"),
                performing_lab=_text(row, "performing_lab"),
                summary_text=_value_with_unit(row),
                file_paths=_files(_text(row, "file_path")),
                **common,
            )
        case DatasetTable.IMAGING_STUDIES:
            return Projection(
                title=_text(row, "exam_name") or "Imaging study",
                category=_text(row, "modality"),
                kind="Outside import" if row.get("is_outside_import") == 1 else None,
                record_date=record_date,
                date_text=None,
                local_time=None,
                ai_category=AiVisibilityCategory.IMAGING_IMPRESSIONS,
                ordered_by=_text(row, "ordered_by"),
                reading_provider=_text(row, "reading_provider"),
                summary_text=_text(row, "impression"),
                body_text=_text(row, "report_text"),
                file_paths=_files(_text(row, "report_file"), _text(row, "pdf_file")),
                **common,
            )
        case DatasetTable.DOCUMENTS:
            folder = (_text(row, "category") or "").casefold()
            kind = _text(row, "kind")
            category = _DOCUMENT_CATEGORIES.get(folder, AiVisibilityCategory.OTHER_DOCUMENTS)
            if kind is not None and kind.casefold() == "avs":
                category = AiVisibilityCategory.VISIT_SUMMARIES
            return Projection(
                title=_text(row, "title") or "Untitled document",
                category=_text(row, "category"),
                kind=kind,
                record_date=record_date,
                date_text=None,
                local_time=None,
                ai_category=category,
                provider=_text(row, "provider"),
                body_text=_text(row, "text"),
                file_paths=_files(_text(row, "file_path")),
                **common,
            )
        case DatasetTable.ENCOUNTERS:
            clock = _parse_clock(_text(row, "time"))
            if record_date is not None and clock is not None:
                local = datetime.combine(record_date, clock)
            visit = _text(row, "visit_type") or "Visit"
            department = _text(row, "department")
            return Projection(
                title=f"{visit} — {department}" if department else visit,
                category=_text(row, "specialty"),
                kind=_text(row, "visit_type"),
                record_date=record_date,
                date_text=_text(row, "time"),
                local_time=local,
                ai_category=AiVisibilityCategory.ENCOUNTERS,
                provider=_text(row, "provider"),
                summary_text=_text(row, "status"),
                file_paths=_files(_text(row, "note_files"), _text(row, "avs_file")),
                **common,
            )
        case DatasetTable.MEDICATIONS:
            start = _text(row, "start_date")
            return Projection(
                title=_text(row, "name") or "Unnamed medication",
                category=None,
                kind="Patient reported" if row.get("patient_reported") == 1 else None,
                record_date=_parse_date(start) or _parse_date(_text(row, "date_noted")),
                date_text=start,
                local_time=None,
                ai_category=None,
                prescriber=_text(row, "prescriber"),
                summary_text=_text(row, "sig"),
                **common,
            )
        case DatasetTable.CONDITIONS:
            onset = _text(row, "onset_date")
            return Projection(
                title=_text(row, "name") or "Unnamed condition",
                category=None,
                kind=None,
                record_date=_parse_date(onset),
                date_text=onset,
                local_time=None,
                ai_category=AiVisibilityCategory.CONDITIONS,
                summary_text=_text(row, "status"),
                **common,
            )
        case DatasetTable.IMMUNIZATIONS:
            return Projection(
                title=_text(row, "name") or "Unnamed immunization",
                category=None,
                kind=None,
                record_date=record_date,
                date_text=None,
                local_time=None,
                ai_category=AiVisibilityCategory.IMMUNIZATIONS,
                summary_text=_text(row, "status"),
                **common,
            )
        case DatasetTable.ALLERGIES:
            return Projection(
                title=_text(row, "name") or "Unnamed allergy",
                category=_text(row, "status"),
                kind=None,
                record_date=None,
                date_text=None,
                local_time=None,
                ai_category=AiVisibilityCategory.ALLERGIES,
                summary_text=_text(row, "reactions"),
                **common,
            )
        case DatasetTable.VITALS:
            local = _parse_local_datetime(_text(row, "datetime"))
            return Projection(
                title=_text(row, "name") or "Vital sign",
                category=None,
                kind=None,
                record_date=record_date,
                date_text=None,
                local_time=local,
                ai_category=AiVisibilityCategory.VITALS,
                summary_text=_value_with_unit(row),
                **common,
            )
        case DatasetTable.SURGICAL_HISTORY:
            when = _text(row, "date_text")
            return Projection(
                title=_text(row, "name") or "Unnamed procedure",
                category=None,
                kind=None,
                record_date=_parse_date(when),
                date_text=when,
                local_time=None,
                ai_category=AiVisibilityCategory.SURGICAL_HISTORY,
                **common,
            )
    raise AssertionError(f"unhandled dataset table {table}")  # pragma: no cover


# ---------------------------------------------------------------------------
# Import
# ---------------------------------------------------------------------------


@dataclass
class _Planned:
    """The diff for one table, computed before anything is written."""

    new: list[dict[str, Any]] = field(default_factory=list)
    changed: list[tuple[HealthRecord, dict[str, Any]]] = field(default_factory=list)
    seen: list[HealthRecord] = field(default_factory=list)
    missing: list[HealthRecord] = field(default_factory=list)


def import_snapshot(
    session: Session,
    *,
    owner_id: uuid.UUID,
    snapshot: DatasetSnapshot,
    dry_run: bool = False,
    started_at: datetime | None = None,
) -> ImportResult:
    started = started_at or datetime.now(UTC)
    _lock(session, owner_id)
    result = ImportResult(dry_run=dry_run, batch_id=None)
    plans: dict[DatasetTable, _Planned] = {}
    for table in DatasetTable:
        plan, counts = _plan_table(
            session, owner_id=owner_id, table=table, rows=snapshot.rows.get(table, [])
        )
        plans[table] = plan
        result.tables[table] = counts
    if dry_run:
        return result

    batch = HealthRecordImportBatch(
        id=uuid.uuid4(),
        owner_id=owner_id,
        dataset_schema_version=snapshot.schema_version,
        dataset_generated_at=snapshot.generated_at,
        source_exported_at=dict(snapshot.source_exported_at),
        snapshot_sha256=snapshot.sha256,
        started_at=started,
        completed_at=started,
        counts={},
    )
    session.add(batch)
    session.flush([batch])
    result.batch_id = batch.id

    for table, plan in plans.items():
        _apply_plan(session, owner_id=owner_id, table=table, plan=plan, batch_id=batch.id)
    session.flush()
    _materialize_labs(session, owner_id=owner_id, batch_id=batch.id, counts=result.labs)

    batch.counts = result.counts()
    batch.completed_at = max(datetime.now(UTC), started)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.INTEGRATION_IMPORT_CONFIRMED,
        target_type="health_record_import_batch",
        target_id=batch.id,
        change_summary=_audit_summary(result),
    )
    session.flush()
    return result


def _audit_summary(result: ImportResult) -> str:
    totals = TableCounts()
    for counts in result.tables.values():
        totals.new += counts.new
        totals.changed += counts.changed
        totals.unchanged += counts.unchanged
        totals.no_longer_in_source += counts.no_longer_in_source
        totals.returned += counts.returned
    labs = result.labs
    return (
        f"health_records;new={totals.new};changed={totals.changed};"
        f"unchanged={totals.unchanged};missing={totals.no_longer_in_source};"
        f"returned={totals.returned};lab_panels_created={labs.panels_created};"
        f"lab_panels_corrected={labs.panels_corrected}"
    )


def _lock(session: Session, owner_id: uuid.UUID) -> None:
    if session.get_bind().dialect.name != "postgresql":
        return
    material = f"health-records-import:{owner_id}".encode()
    key = int.from_bytes(hashlib.sha256(material).digest()[:8], "big", signed=True)
    session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": key})


def _plan_table(
    session: Session,
    *,
    owner_id: uuid.UUID,
    table: DatasetTable,
    rows: Iterable[dict[str, Any]],
) -> tuple[_Planned, TableCounts]:
    existing: dict[str, tuple[HealthRecord, str]] = {
        envelope.record_id: (envelope, digest)
        for envelope, digest in session.execute(
            select(HealthRecord, HealthRecordRevision.content_sha256)
            .join(
                HealthRecordRevision,
                (HealthRecordRevision.health_record_id == HealthRecord.id)
                & (HealthRecordRevision.revision == HealthRecord.current_revision),
            )
            .where(HealthRecord.owner_id == owner_id, HealthRecord.dataset_table == table)
        ).tuples()
    }
    plan = _Planned()
    counts = TableCounts()
    present: set[str] = set()
    for row in rows:
        record_id = str(row["record_id"])
        present.add(record_id)
        known = existing.get(record_id)
        if known is None:
            plan.new.append(row)
            counts.new += 1
            continue
        envelope, digest = known
        plan.seen.append(envelope)
        if envelope.state is RecordState.NO_LONGER_IN_SOURCE:
            counts.returned += 1
        if digest != content_hash(row):
            plan.changed.append((envelope, row))
            counts.changed += 1
        else:
            counts.unchanged += 1
    for record_id, (envelope, _) in existing.items():
        if record_id not in present and envelope.state is RecordState.PRESENT:
            plan.missing.append(envelope)
            counts.no_longer_in_source += 1
    return plan, counts


def _apply_projection(envelope: HealthRecord, projection: Projection) -> None:
    envelope.title = projection.title
    envelope.category = _clip(projection.category, 120)
    envelope.kind = _clip(projection.kind, 120)
    envelope.record_date = projection.record_date
    envelope.date_text = _clip(projection.date_text, 100)
    envelope.local_time = projection.local_time
    envelope.timezone = projection.timezone
    envelope.timezone_assumed = projection.timezone_assumed
    envelope.ai_category = projection.ai_category


def _revision(
    *,
    owner_id: uuid.UUID,
    envelope_id: uuid.UUID,
    number: int,
    batch_id: uuid.UUID,
    row: Mapping[str, Any],
    projection: Projection,
) -> HealthRecordRevision:
    return HealthRecordRevision(
        id=uuid.uuid4(),
        owner_id=owner_id,
        health_record_id=envelope_id,
        revision=number,
        batch_id=batch_id,
        content_sha256=content_hash(row),
        provider=_clip(projection.provider, 300),
        ordered_by=_clip(projection.ordered_by, 300),
        reading_provider=_clip(projection.reading_provider, 300),
        performing_lab=_clip(projection.performing_lab, 300),
        prescriber=_clip(projection.prescriber, 300),
        summary_text=projection.summary_text,
        body_text=projection.body_text,
        file_paths=list(projection.file_paths),
        source_row={
            key: value for key, value in _jsonable_row(row).items() if key not in _LONG_TEXT_COLUMNS
        },
    )


def _apply_plan(
    session: Session,
    *,
    owner_id: uuid.UUID,
    table: DatasetTable,
    plan: _Planned,
    batch_id: uuid.UUID,
) -> None:
    for row in plan.new:
        projection = project(table, row)
        envelope = HealthRecord(
            id=uuid.uuid4(),
            owner_id=owner_id,
            dataset_table=table,
            record_id=str(row["record_id"]),
            source=_clip(str(row["source"]).strip(), 200),
            organization=_clip(str(row["organization"]).strip(), 300),
            state=RecordState.PRESENT,
            first_seen_batch_id=batch_id,
            last_seen_batch_id=batch_id,
            current_revision=1,
            title=projection.title,
        )
        _apply_projection(envelope, projection)
        session.add(envelope)
        session.add(
            _revision(
                owner_id=owner_id,
                envelope_id=envelope.id,
                number=1,
                batch_id=batch_id,
                row=row,
                projection=projection,
            )
        )
    for envelope, row in plan.changed:
        projection = project(table, row)
        envelope.current_revision += 1
        envelope.source = _clip(str(row["source"]).strip(), 200) or envelope.source
        envelope.organization = (
            _clip(str(row["organization"]).strip(), 300) or envelope.organization
        )
        _apply_projection(envelope, projection)
        session.add(
            _revision(
                owner_id=owner_id,
                envelope_id=envelope.id,
                number=envelope.current_revision,
                batch_id=batch_id,
                row=row,
                projection=projection,
            )
        )
    for envelope in plan.seen:
        envelope.last_seen_batch_id = batch_id
        envelope.state = RecordState.PRESENT
        envelope.missing_since_batch_id = None
    for envelope in plan.missing:
        envelope.state = RecordState.NO_LONGER_IN_SOURCE
        envelope.missing_since_batch_id = batch_id


# ---------------------------------------------------------------------------
# Labs
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _LabRow:
    record_id: str
    digest: str
    row: dict[str, Any]


def _panel_key(row: Mapping[str, Any]) -> tuple[str, str, str, str]:
    when = _text(row, "datetime") or _text(row, "date") or ""
    return (
        _text(row, "source") or "",
        _text(row, "organization") or "",
        when,
        _text(row, "panel") or "",
    )


def _panel_provider_id(owner_id: uuid.UUID, key: tuple[str, str, str, str]) -> str:
    # The shared provider-identity index on lab_panel is not owner-scoped, so the owner
    # is part of the identity.
    digest = hashlib.sha256(json.dumps([str(owner_id), *key]).encode()).hexdigest()
    return LAB_PANEL_PROVIDER_PREFIX + digest


def _candidate(index: int, lab: _LabRow) -> LabCandidate | None:
    row = lab.row
    value_text = _text(row, "value_text")
    value_num = row.get("value_num")
    numeric = isinstance(value_num, int | float) and math.isfinite(float(value_num))
    if value_text is None and not numeric:
        return None
    original_value: str | None = None
    qualitative: str | None = None
    if numeric:
        original_value = value_text if value_text is not None else repr(value_num)
    else:
        qualitative = value_text
    return LabCandidate(
        source_row_index=index,
        analyte_name=_text(row, "test") or _text(row, "test_normalized") or "Unnamed test",
        original_value=original_value,
        qualitative_result=qualitative,
        original_unit=_text(row, "units"),
        original_reference_range=_text(row, "ref_range"),
        abnormal_flag=_text(row, "flag"),
        source_normalized_name=_clip(_text(row, "test_normalized"), 300),
        source_record_id=lab.record_id,
    )


def _shared(rows: Iterable[_LabRow], column: str) -> str | None:
    values = {_text(lab.row, column) for lab in rows}
    return values.pop() if len(values) == 1 else None


def _materialize_labs(
    session: Session, *, owner_id: uuid.UUID, batch_id: uuid.UUID, counts: LabCounts
) -> None:
    groups: dict[tuple[str, str, str, str], list[_LabRow]] = defaultdict(list)
    for envelope, revision in session.execute(
        select(HealthRecord, HealthRecordRevision)
        .join(
            HealthRecordRevision,
            (HealthRecordRevision.health_record_id == HealthRecord.id)
            & (HealthRecordRevision.revision == HealthRecord.current_revision),
        )
        .where(
            HealthRecord.owner_id == owner_id,
            HealthRecord.dataset_table == DatasetTable.LAB_RESULTS,
        )
    ).tuples():
        row = dict(revision.source_row)
        groups[_panel_key(row)].append(_LabRow(envelope.record_id, revision.content_sha256, row))

    superseded = select(LabPanel.supersedes_id).where(
        LabPanel.owner_id == owner_id, LabPanel.supersedes_id.is_not(None)
    )
    current_panels = {
        panel.provider_id: panel
        for panel in session.scalars(
            select(LabPanel).where(
                LabPanel.owner_id == owner_id,
                LabPanel.source_type == SourceType.HEALTH_RECORDS_IMPORT,
                LabPanel.id.not_in(superseded),
            )
        )
    }

    for key in sorted(groups):
        labs = sorted(groups[key], key=lambda lab: lab.record_id)
        candidates: list[LabCandidate] = []
        for lab in labs:
            candidate = _candidate(len(candidates), lab)
            if candidate is None:
                counts.results_without_value += 1
            else:
                candidates.append(candidate)
        if not candidates:
            continue
        revision = hashlib.sha256(
            json.dumps([[lab.record_id, lab.digest] for lab in labs]).encode()
        ).hexdigest()
        provider_id = _panel_provider_id(owner_id, key)
        existing = current_panels.get(provider_id)
        if existing is not None and existing.source_revision == revision:
            counts.panels_unchanged += 1
            continue
        _, organization, when, panel_name = key
        zone, _ = timezone_for(organization)
        local = _parse_local_datetime(when)
        if local is None:
            day = _parse_date(when)
            if day is None:
                counts.results_without_value += len(candidates)
                continue
            local = datetime.combine(day, DATE_ONLY_TIME)
        collected = resolve_wall_time(local, zone)
        create_panel(
            session,
            owner_id=owner_id,
            specimen_time=collected,
            # Owner decision (ADR-0040): the report time is the collection time.
            report_time=collected,
            candidates=candidates,
            source_type=SourceType.HEALTH_RECORDS_IMPORT,
            confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
            provider_id=provider_id,
            source_revision=revision,
            laboratory_name=_clip(_shared(labs, "performing_lab"), 300),
            specimen_type=_clip(_shared(labs, "specimen"), 255),
            panel_name=_clip(panel_name, 300) or None,
            source_organization=_clip(organization, 300),
            ordered_by=_clip(_shared(labs, "ordered_by"), 300),
            supersedes_id=existing.id if existing is not None else None,
            correction_reason=LAB_CORRECTION_REASON if existing is not None else None,
            import_batch_id=batch_id,
        )
        if existing is None:
            counts.panels_created += 1
        else:
            counts.panels_corrected += 1
