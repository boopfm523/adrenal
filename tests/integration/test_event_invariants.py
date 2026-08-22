"""Canonical event invariants, enforced by the database.

Application code can be bypassed by a bug, a migration, or a direct SQL fix. These
tests assert the constraints hold at the storage layer, which is the only place an
invariant is genuinely safe. ADR-0001 is why they require real PostgreSQL: partial
unique indexes and check constraints do not behave the same on SQLite.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.db import SCHEMAS, Base
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import SymptomEvent
from healthcurve.events.timekeeping import resolve_event_time

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

LONDON = "Europe/London"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        eng = create_engine(container.get_connection_url())
        with eng.begin() as conn:
            # All bases share one MetaData, so create_all builds the whole schema and
            # every namespace has to exist first.
            for schema in SCHEMAS:
                conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
            conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        Base.metadata.create_all(eng)
        yield eng
        eng.dispose()


@pytest.fixture(scope="module")
def owner_id(engine: Engine) -> uuid.UUID:
    """Events are owner-scoped, so the FK target has to exist."""
    from sqlalchemy.orm import sessionmaker

    from healthcurve.identity.models import Owner

    factory = sessionmaker(engine, expire_on_commit=False)
    owner = Owner(
        id=uuid.uuid4(),
        email="invariants@example.com",
        password_hash="not-a-real-hash",
        default_timezone="Europe/London",
    )
    identifier = owner.id
    with factory() as session, session.begin():
        session.add(owner)
    return identifier


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(engine)
    with factory() as s:
        yield s
        s.rollback()


def make_symptom(owner_id: uuid.UUID, **overrides: object) -> SymptomEvent:
    """A valid symptom event; overrides let each test break exactly one thing."""
    resolved = resolve_event_time(datetime(2026, 1, 15, 9, 0), LONDON)  # noqa: DTZ001
    event = SymptomEvent(
        owner_id=owner_id,
        name="fatigue",
        severity=5,
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        recorded_at=datetime(2026, 1, 15, 10, 0, tzinfo=UTC),
    )
    event.apply_event_time(resolved)
    for key, value in overrides.items():
        setattr(event, key, value)
    return event


# ---------------------------------------------------------------------------
# Provenance is mandatory (SAFE-09)
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-09")
@pytest.mark.parametrize(
    "field",
    [
        "occurred_at",
        "local_time",
        "timezone",
        "utc_offset_minutes",
        "source_type",
        "confirmation_state",
    ],
)
def test_provenance_fields_cannot_be_null(
    session: Session, owner_id: uuid.UUID, field: str
) -> None:
    """There is no nullable escape hatch for the fields that make a record trustworthy."""
    session.add(make_symptom(owner_id, **{field: None}))
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.safety("SAFE-09")
def test_round_trip_preserves_all_four_time_fields(session: Session, owner_id: uuid.UUID) -> None:
    event = make_symptom(
        owner_id,
    )
    event.apply_event_time(resolve_event_time(datetime(2026, 7, 15, 7, 8), LONDON))  # noqa: DTZ001
    event.recorded_at = event.occurred_at + timedelta(minutes=2)
    session.add(event)
    session.flush()
    session.expire_all()

    stored = session.get(SymptomEvent, event.id)
    assert stored is not None
    assert stored.occurred_at.astimezone(UTC) == datetime(2026, 7, 15, 6, 8, tzinfo=UTC)
    assert stored.local_time == datetime(2026, 7, 15, 7, 8)  # noqa: DTZ001
    assert stored.timezone == LONDON
    assert stored.utc_offset_minutes == 60


def test_offset_outside_the_real_world_range_is_rejected(
    session: Session, owner_id: uuid.UUID
) -> None:
    session.add(make_symptom(owner_id, utc_offset_minutes=1000))
    with pytest.raises(IntegrityError, match="offset_within_real_range"):
        session.flush()


def test_an_event_cannot_be_recorded_before_it_occurred(
    session: Session, owner_id: uuid.UUID
) -> None:
    event = make_symptom(
        owner_id,
    )
    event.recorded_at = event.occurred_at - timedelta(hours=1)
    session.add(event)
    with pytest.raises(IntegrityError, match="recorded_after_occurred"):
        session.flush()


def test_severity_must_be_on_the_defined_scale(session: Session, owner_id: uuid.UUID) -> None:
    session.add(make_symptom(owner_id, severity=11))
    with pytest.raises(IntegrityError, match="severity_scale"):
        session.flush()


# ---------------------------------------------------------------------------
# Corrections supersede, never overwrite (SAFE-08)
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-08")
def test_a_correction_retains_the_original(session: Session, owner_id: uuid.UUID) -> None:
    original = make_symptom(owner_id, severity=5)
    session.add(original)
    session.flush()
    original_id = original.id

    correction = make_symptom(owner_id, severity=8)
    correction.supersedes_id = original_id
    correction.correction_reason = "misremembered severity"
    session.add(correction)
    session.flush()
    session.expire_all()

    retained = session.get(SymptomEvent, original_id)
    assert retained is not None, "the superseded row must remain queryable"
    assert retained.severity == 5, "the original value must not be rewritten"
    assert retained.supersedes_id is None

    stored_correction = session.get(SymptomEvent, correction.id)
    assert stored_correction is not None
    assert stored_correction.severity == 8
    assert stored_correction.is_correction


@pytest.mark.safety("SAFE-08")
def test_a_row_can_be_superseded_only_once(session: Session, owner_id: uuid.UUID) -> None:
    """Two corrections of the same row would make "the current version" ambiguous."""
    original = make_symptom(
        owner_id,
    )
    session.add(original)
    session.flush()

    first = make_symptom(owner_id, severity=8, supersedes_id=original.id)
    session.add(first)
    session.flush()

    second = make_symptom(owner_id, severity=9, supersedes_id=original.id)
    session.add(second)
    with pytest.raises(IntegrityError, match="supersedes_once"):
        session.flush()


@pytest.mark.safety("SAFE-08")
def test_a_record_cannot_correct_itself(session: Session, owner_id: uuid.UUID) -> None:
    event = make_symptom(
        owner_id,
    )
    event.id = uuid.uuid4()
    event.supersedes_id = event.id
    session.add(event)
    with pytest.raises(IntegrityError, match="no_self_supersede"):
        session.flush()


@pytest.mark.safety("SAFE-08")
def test_a_superseded_row_cannot_be_deleted(session: Session, owner_id: uuid.UUID) -> None:
    """ON DELETE RESTRICT: deleting the original would orphan the correction history."""
    original = make_symptom(
        owner_id,
    )
    session.add(original)
    session.flush()

    session.add(make_symptom(owner_id, severity=8, supersedes_id=original.id))
    session.flush()

    session.delete(original)
    with pytest.raises(IntegrityError):
        session.flush()


@pytest.mark.safety("SAFE-08")
def test_correction_chains_are_linear_and_walkable(session: Session, owner_id: uuid.UUID) -> None:
    """A -> B -> C: each step retained, and the head is unambiguous."""
    first = make_symptom(owner_id, severity=3)
    session.add(first)
    session.flush()

    second = make_symptom(owner_id, severity=5, supersedes_id=first.id)
    session.add(second)
    session.flush()

    third = make_symptom(owner_id, severity=7, supersedes_id=second.id)
    session.add(third)
    session.flush()
    session.expire_all()

    superseded_ids = set(
        session.scalars(
            select(SymptomEvent.supersedes_id).where(SymptomEvent.supersedes_id.isnot(None))
        )
    )
    heads = [e for e in session.scalars(select(SymptomEvent)) if e.id not in superseded_ids]
    assert len(heads) == 1
    assert heads[0].severity == 7

    # Every earlier version is still there with its original value.
    assert {e.severity for e in session.scalars(select(SymptomEvent))} == {3, 5, 7}


# ---------------------------------------------------------------------------
# Provider imports are idempotent
# ---------------------------------------------------------------------------


def test_the_same_provider_record_cannot_be_imported_twice(
    session: Session, owner_id: uuid.UUID
) -> None:
    session.add(
        make_symptom(
            owner_id,
            source_type=SourceType.PROVIDER,
            confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
            provider_id="garmin-123",
            source_revision="rev-1",
        )
    )
    session.flush()

    session.add(
        make_symptom(
            owner_id,
            source_type=SourceType.PROVIDER,
            confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
            provider_id="garmin-123",
            source_revision="rev-1",
        )
    )
    with pytest.raises(IntegrityError, match="provider_identity"):
        session.flush()


def test_a_revised_provider_record_is_a_separate_row(session: Session, owner_id: uuid.UUID) -> None:
    """A provider revision must be storable so the two can be reconciled."""
    for revision in ("rev-1", "rev-2"):
        session.add(
            make_symptom(
                owner_id,
                source_type=SourceType.PROVIDER,
                confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
                provider_id="garmin-456",
                source_revision=revision,
            )
        )
    session.flush()

    stored = session.scalars(
        select(SymptomEvent).where(SymptomEvent.provider_id == "garmin-456")
    ).all()
    assert len({e.source_revision for e in stored}) == 2


def test_provider_can_return_to_an_earlier_revision(session: Session, owner_id: uuid.UUID) -> None:
    """Provider state A -> B -> A remains a complete, linear correction history."""
    first_recorded_at = datetime(2026, 1, 15, 10, 0, tzinfo=UTC)
    first = make_symptom(
        owner_id,
        source_type=SourceType.PROVIDER,
        confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
        provider_id="garmin-returned-state",
        source_revision="rev-a",
        recorded_at=first_recorded_at,
    )
    session.add(first)
    session.flush()

    second = make_symptom(
        owner_id,
        source_type=SourceType.PROVIDER,
        confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
        provider_id="garmin-returned-state",
        source_revision="rev-b",
        supersedes_id=first.id,
        recorded_at=first_recorded_at + timedelta(seconds=1),
    )
    session.add(second)
    session.flush()

    third = make_symptom(
        owner_id,
        source_type=SourceType.PROVIDER,
        confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
        provider_id="garmin-returned-state",
        source_revision="rev-a",
        supersedes_id=second.id,
        recorded_at=first_recorded_at + timedelta(seconds=2),
    )
    session.add(third)
    session.flush()

    stored = session.scalars(
        select(SymptomEvent)
        .where(SymptomEvent.provider_id == "garmin-returned-state")
        .order_by(SymptomEvent.recorded_at, SymptomEvent.id)
    ).all()
    assert [event.source_revision for event in stored] == ["rev-a", "rev-b", "rev-a"]
    assert stored[1].supersedes_id == stored[0].id
    assert stored[2].supersedes_id == stored[1].id


def test_manually_entered_events_are_not_constrained_by_provider_identity(
    session: Session,
    owner_id: uuid.UUID,
) -> None:
    """The idempotency index is partial: two identical manual entries are both valid.

    Someone can genuinely take the same medication twice, or report the same symptom
    twice. Only provider rows carry an external identity to deduplicate on.
    """
    for _ in range(3):
        session.add(make_symptom(owner_id, provider_id=None, source_revision=None))
    session.flush()

    assert len(session.scalars(select(SymptomEvent)).all()) == 3
