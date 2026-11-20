"""Owner opt-in for imported health records in chat and AI (ADR-0040 section 5).

Every category starts hidden. Medication lists have no category and can never be
enabled. The database views read the same table, so a code path that forgets to call
:func:`imported_labs_visible_to_ai` still cannot surface imported labs through SQL chat.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.events.base import SourceType
from healthcurve.integrations.health_records.models import (
    AiVisibilityCategory,
    DatasetTable,
    HealthRecord,
    HealthRecordAiVisibility,
    RecordState,
)
from healthcurve.labs.models import LabPanel, LabResult
from healthcurve.operations import audit


def enabled_categories(session: Session, *, owner_id: uuid.UUID) -> set[AiVisibilityCategory]:
    return set(
        session.scalars(
            select(HealthRecordAiVisibility.category).where(
                HealthRecordAiVisibility.owner_id == owner_id,
                HealthRecordAiVisibility.enabled.is_(True),
            )
        )
    )


def set_category(
    session: Session,
    *,
    owner_id: uuid.UUID,
    category: AiVisibilityCategory,
    enabled: bool,
) -> bool:
    """Set one category. Returns True when the stored value changed."""
    row = session.get(HealthRecordAiVisibility, (owner_id, category))
    if row is not None and row.enabled == enabled:
        return False
    if row is None:
        if not enabled:
            return False
        row = HealthRecordAiVisibility(owner_id=owner_id, category=category, enabled=enabled)
        session.add(row)
    row.enabled = enabled
    row.changed_at = datetime.now(UTC)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.HEALTH_RECORD_AI_VISIBILITY_CHANGED,
        target_type="health_record_ai_visibility",
        target_id=owner_id,
        change_summary=f"category={category.value};enabled={str(enabled).lower()}",
    )
    session.flush()
    return True


def imported_labs_visible_to_ai(session: Session, *, owner_id: uuid.UUID) -> bool:
    return AiVisibilityCategory.LABS in enabled_categories(session, owner_id=owner_id)


def withdrawn_lab_record_ids(session: Session, *, owner_id: uuid.UUID) -> set[str]:
    """Imported lab ``record_id``s the latest snapshot no longer contains."""
    return set(
        session.scalars(
            select(HealthRecord.record_id).where(
                HealthRecord.owner_id == owner_id,
                HealthRecord.dataset_table == DatasetTable.LAB_RESULTS,
                HealthRecord.state == RecordState.NO_LONGER_IN_SOURCE,
            )
        )
    )


def ai_visible_panels(
    session: Session, *, owner_id: uuid.UUID, panels: list[LabPanel]
) -> list[tuple[LabPanel, list[LabResult]]]:
    """Filter current panels for an AI input: imported ones only when opted in, and
    never a result the source has since withdrawn. Owner-entered panels pass as-is."""
    imported_visible = imported_labs_visible_to_ai(session, owner_id=owner_id)
    withdrawn = withdrawn_lab_record_ids(session, owner_id=owner_id) if imported_visible else set()
    visible: list[tuple[LabPanel, list[LabResult]]] = []
    for panel in panels:
        if panel.source_type is not SourceType.HEALTH_RECORDS_IMPORT:
            visible.append((panel, list(panel.results)))
            continue
        if not imported_visible:
            continue
        results = [r for r in panel.results if r.source_record_id not in withdrawn]
        if results:
            visible.append((panel, results))
    return visible
