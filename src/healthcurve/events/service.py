"""Creating and correcting recorded facts.

One implementation for every event type, so provenance and correction behaviour cannot
drift between doses, symptoms, and injections. Anything that writes a fact goes through
here.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from healthcurve.events.base import ConfirmationState, EventMixin, SourceType
from healthcurve.events.timekeeping import EventTime, resolve_event_time
from healthcurve.operations import audit


class CorrectionError(Exception):
    """A correction that would break the supersession rules (SAFE-08)."""


def build_event_time(local_time: datetime, timezone: str, fold: int | None = None) -> EventTime:
    """Resolve a submitted local time. Raises on ambiguous or nonexistent times."""
    naive = local_time.replace(tzinfo=None) if local_time.tzinfo else local_time
    return resolve_event_time(naive, timezone, fold=fold)


def create_event[E: EventMixin](
    session: Session,
    model: type[E],
    *,
    owner_id: uuid.UUID,
    event_time: EventTime,
    source_type: SourceType,
    confirmation_state: ConfirmationState,
    correlation_id: str | None = None,
    **fields: Any,
) -> E:
    """Create a fact with full provenance and an audit entry."""
    # Built with the type-specific fields, then the shared provenance is applied.
    # The mixin's columns are not visible to a type checker as constructor arguments,
    # and assigning them explicitly is clearer than suppressing that.
    event = model(**fields)
    event.owner_id = owner_id
    event.source_type = source_type
    event.confirmation_state = confirmation_state
    event.recorded_at = datetime.now(UTC)
    event.apply_event_time(event_time)
    session.add(event)
    session.flush()

    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.RECORD_CREATED,
        target_type=model.__tablename__,
        target_id=event.id,
        correlation_id=correlation_id,
    )
    return event


#: Never copied onto a correction -- each is either identity, provenance of the new
#: row, or the link that makes it a correction in the first place.
_NON_COPYABLE = frozenset(
    {"id", "recorded_at", "supersedes_id", "correction_reason", "_sa_instance_state"}
)


def correct_event[E: EventMixin](
    session: Session,
    model: type[E],
    original: E,
    *,
    reason: str,
    changes: dict[str, Any],
    event_time: EventTime | None = None,
    correlation_id: str | None = None,
) -> E:
    """Supersede ``original`` with a corrected copy. The original is never modified.

    Correcting an already-superseded row is refused: the chain must stay linear so
    "the current version" is never ambiguous. Correct the head instead.
    """
    already = session.scalar(select(model).where(model.supersedes_id == original.id))
    if already is not None:
        raise CorrectionError(
            "this record has already been corrected; correct the current version instead"
        )

    mapper = inspect(model, raiseerr=True)
    columns = {c.name for c in mapper.columns}
    unknown = set(changes) - columns
    if unknown:
        raise CorrectionError(f"unknown field(s): {sorted(unknown)}")

    carried = {
        name: getattr(original, name)
        for name in columns
        if name not in _NON_COPYABLE and name not in changes
    }
    carried.update(changes)
    carried.pop("owner_id", None)

    correction = model(**carried)
    correction.owner_id = original.owner_id
    correction.recorded_at = datetime.now(UTC)
    correction.supersedes_id = original.id
    correction.correction_reason = reason
    if event_time is not None:
        correction.apply_event_time(event_time)

    session.add(correction)
    session.flush()

    audit.record(
        session,
        actor=audit.actor_for_owner(original.owner_id),
        action=audit.AuditAction.RECORD_CORRECTED,
        target_type=model.__tablename__,
        target_id=correction.id,
        correlation_id=correlation_id,
        # Field names only -- putting the values here would put health data in the log.
        change_summary=f"superseded {original.id}; changed: {sorted(changes)}",
    )
    return correction


def current_only[E: EventMixin](session: Session, model: type[E], rows: list[E]) -> list[E]:
    """Drop rows that a correction supersedes.

    Filtering in Python rather than SQL because the superseding row may fall outside
    the caller's date window -- a dose corrected next week still supersedes today's.
    """
    if not rows:
        return rows
    ids = [r.id for r in rows]
    superseded = set(
        session.scalars(select(model.supersedes_id).where(model.supersedes_id.in_(ids)))
    )
    return [r for r in rows if r.id not in superseded]


def current_fact_predicate[E: EventMixin](
    model: type[E], *, owner_id: uuid.UUID
) -> ColumnElement[bool]:
    """SQL predicate that excludes superseded revisions before rows are materialized.

    This is equivalent to :func:`current_only`, including a correction recorded
    outside the requested time window, but does not send every selected ID back to
    PostgreSQL in a second potentially enormous ``IN`` query.  It is the appropriate
    shape for dense wearable and long report windows.
    """
    return model.id.not_in(
        select(model.supersedes_id).where(
            model.owner_id == owner_id,
            model.supersedes_id.is_not(None),
        )
    )


def is_superseded[E: EventMixin](session: Session, model: type[E], event_id: uuid.UUID) -> bool:
    return session.scalar(select(model.id).where(model.supersedes_id == event_id)) is not None
