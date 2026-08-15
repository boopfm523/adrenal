from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.db import SCHEMAS, Base
from healthcurve.episodes.models import EpisodeSeverity, EpisodeStatus, StressEpisode
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import DiaryEvent, SymptomEvent
from healthcurve.events.timekeeping import resolve_event_time
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.models import (
    GarminMetricEvent,
    GarminMetricType,
    GarminSyncRun,
    GarminSyncStatus,
)
from healthcurve.integrations.telegram.models import TelegramDoseReminder
from healthcurve.labs.models import LabPanel, LabResult
from healthcurve.medications import service as medications
from healthcurve.medications.models import (
    ApprovedInstruction,
    DoseCategory,
    DoseEvent,
    DoseUnit,
    InstructionCategory,
    Medication,
    RegimenDoseSlot,
    RegimenVersion,
    Route,
)
from healthcurve.operations.audit import AuditAction, AuditEntry
from healthcurve.selective_test_data_cleanup import (
    SelectiveTestDataCleanupError,
    execute_selective_test_data_reset,
    preview_selective_test_data_reset,
)
from healthcurve.vitals.models import (
    BloodPressureEvent,
    MeasurementSetting,
    WeightEvent,
    WeightUnit,
)

pytestmark = [pytest.mark.postgres, pytest.mark.slow]
ZONE = "America/New_York"


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        eng = create_engine(container.get_connection_url())
        with eng.begin() as connection:
            for schema in SCHEMAS:
                connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        Base.metadata.create_all(eng)
        yield eng
        eng.dispose()


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as database:
        yield database
        database.rollback()


def _seed_mixed_data(session: Session) -> Owner:
    owner = Owner(
        email=f"selective-reset-{uuid.uuid4()}@example.test",
        password_hash="synthetic-not-a-password",
        default_timezone=ZONE,
    )
    session.add(owner)
    session.flush()
    medication = Medication(
        owner_id=owner.id,
        name="Synthetic medication vocabulary",
        normalized_name=f"synthetic-medication-{uuid.uuid4()}",
        default_unit=DoseUnit.MG,
        default_route=Route.ORAL,
    )
    session.add(medication)
    session.flush()
    version = medications.create_draft(
        session,
        owner_id=owner.id,
        version_label="Owner-declared test plan",
        effective_from=datetime(2026, 8, 1),  # noqa: DTZ001
        effective_timezone=ZONE,
    )
    slot = RegimenDoseSlot(
        regimen_version_id=version.id,
        medication_id=medication.id,
        scheduled_local_time=time(7),
        amount=Decimal("10"),
        unit=DoseUnit.MG,
        route=Route.ORAL,
    )
    instruction = ApprovedInstruction(
        regimen_version_id=version.id,
        category=InstructionCategory.GENERAL,
        title="Synthetic instruction",
        body="Synthetic body",
        authored_by="Synthetic clinician",
        authored_on=date(2026, 8, 1),
    )
    session.add_all([slot, instruction])
    session.flush()
    session.add(
        TelegramDoseReminder(
            owner_id=owner.id,
            regimen_version_id=version.id,
            slot_id=slot.id,
            local_date=date(2026, 8, 15),
            scheduled_at=datetime(2026, 8, 15, 11, tzinfo=UTC),
            due_at=datetime(2026, 8, 15, 11, 30, tzinfo=UTC),
        )
    )
    episode = StressEpisode(
        owner_id=owner.id,
        trigger="Synthetic episode",
        status=EpisodeStatus.RESOLVED,
        severity=EpisodeSeverity.MILD,
        started_at=datetime(2026, 8, 14, 14, tzinfo=UTC),
        ended_at=datetime(2026, 8, 14, 15, tzinfo=UTC),
        timezone=ZONE,
        recorded_at=datetime(2026, 8, 14, 16, tzinfo=UTC),
    )
    session.add(episode)
    session.flush()

    original_dose = _dose(session, owner, medication, version, slot, episode)
    _dose(
        session,
        owner,
        medication,
        version,
        slot,
        episode,
        minute=5,
        supersedes_id=original_dose.id,
    )
    original_symptom = _symptom(session, owner, episode, severity=3)
    _symptom(session, owner, episode, severity=4, supersedes_id=original_symptom.id)

    # Preserved real-data domains.
    events.create_event(
        session,
        BloodPressureEvent,
        owner_id=owner.id,
        event_time=resolve_event_time(datetime(2026, 8, 15, 8), ZONE),  # noqa: DTZ001
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        systolic_mmhg=121,
        diastolic_mmhg=81,
        pulse_bpm=70,
        measurement_setting=MeasurementSetting.HOME,
    )
    events.create_event(
        session,
        WeightEvent,
        owner_id=owner.id,
        event_time=resolve_event_time(datetime(2026, 8, 15, 8, 5), ZONE),  # noqa: DTZ001
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        value=Decimal("180"),
        unit=WeightUnit.LB,
        normalized_kg=Decimal("81.6466"),
        measurement_setting=MeasurementSetting.HOME,
    )
    events.create_event(
        session,
        DiaryEvent,
        owner_id=owner.id,
        event_time=resolve_event_time(datetime(2026, 8, 15, 8, 10), ZONE),  # noqa: DTZ001
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        text="Synthetic fixture diary text",
        is_sensitive=False,
    )
    _garmin_metric(session, owner)
    _lab_result(session, owner)
    session.flush()
    return owner


def _dose(
    session: Session,
    owner: Owner,
    medication: Medication,
    version: RegimenVersion,
    slot: RegimenDoseSlot,
    episode: StressEpisode,
    *,
    minute: int = 0,
    supersedes_id: uuid.UUID | None = None,
) -> DoseEvent:
    return events.create_event(
        session,
        DoseEvent,
        owner_id=owner.id,
        event_time=resolve_event_time(datetime(2026, 8, 14, 7, minute), ZONE),  # noqa: DTZ001
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        medication_id=medication.id,
        amount=Decimal("10"),
        unit=DoseUnit.MG,
        route=Route.ORAL,
        category=DoseCategory.SCHEDULED,
        regimen_version_id=version.id,
        slot_id=slot.id,
        episode_id=episode.id,
        supersedes_id=supersedes_id,
        correction_reason="Synthetic correction" if supersedes_id else None,
    )


def _symptom(
    session: Session,
    owner: Owner,
    episode: StressEpisode,
    *,
    severity: int,
    supersedes_id: uuid.UUID | None = None,
) -> SymptomEvent:
    return events.create_event(
        session,
        SymptomEvent,
        owner_id=owner.id,
        event_time=resolve_event_time(datetime(2026, 8, 14, 8), ZONE),  # noqa: DTZ001
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        name="synthetic symptom",
        severity=severity,
        episode_id=episode.id,
        supersedes_id=supersedes_id,
        correction_reason="Synthetic correction" if supersedes_id else None,
    )


def _garmin_metric(session: Session, owner: Owner) -> None:
    sync = GarminSyncRun(
        owner_id=owner.id,
        requested_start_date=date(2026, 8, 15),
        requested_end_date=date(2026, 8, 15),
        timezone=ZONE,
        status=GarminSyncStatus.COMPLETED,
        started_at=datetime(2026, 8, 15, 12, tzinfo=UTC),
        finished_at=datetime(2026, 8, 15, 12, 1, tzinfo=UTC),
        counts={},
        warning_codes=[],
        client_version="synthetic",
    )
    session.add(sync)
    session.flush()
    events.create_event(
        session,
        GarminMetricEvent,
        owner_id=owner.id,
        event_time=resolve_event_time(datetime(2026, 8, 15, 8, 15), ZONE),  # noqa: DTZ001
        source_type=SourceType.PROVIDER,
        confirmation_state=ConfirmationState.PROVIDER_IMPORTED,
        metric_type=GarminMetricType.HEART_RATE,
        value=Decimal("65"),
        unit="bpm",
        aggregation="provider_sample",
        garmin_sync_run_id=sync.id,
        garmin_source_member="provider:synthetic",
        garmin_manufacturer="Garmin",
        garmin_field_name="heart_rate",
    )


def _lab_result(session: Session, owner: Owner) -> None:
    panel = events.create_event(
        session,
        LabPanel,
        owner_id=owner.id,
        event_time=resolve_event_time(datetime(2026, 8, 15, 8, 20), ZONE),  # noqa: DTZ001
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        reported_at=datetime(2026, 8, 15, 13, tzinfo=UTC),
        reported_local_time=datetime(2026, 8, 15, 9),  # noqa: DTZ001
        reported_timezone=ZONE,
        reported_utc_offset_minutes=-240,
    )
    session.add(
        LabResult(
            owner_id=owner.id,
            panel_id=panel.id,
            analyte_name="Synthetic analyte",
            original_value="1",
        )
    )


def _count(session: Session, model: type, owner_id: uuid.UUID) -> int:
    return (
        session.scalar(select(func.count()).select_from(model).where(model.owner_id == owner_id))
        or 0
    )


def test_preview_then_execute_clears_only_declared_domains(session: Session) -> None:
    owner = _seed_mixed_data(session)
    preview = preview_selective_test_data_reset(session, owner_id=owner.id)
    assert preview.counts.regimen_versions == 1
    assert preview.counts.dose_events == 2
    assert preview.counts.stress_episodes == 1
    assert preview.counts.symptom_events == 2
    assert preview.preserved.blood_pressure_events == 1
    assert preview.preserved.weight_events == 1
    assert preview.preserved.garmin_metric_events == 1
    assert preview.preserved.lab_results == 1

    result = execute_selective_test_data_reset(
        session,
        owner_id=owner.id,
        preview=preview,
        confirmation=preview.confirmation_phrase,
    )
    assert result == preview.counts
    assert _count(session, RegimenVersion, owner.id) == 0
    assert _count(session, DoseEvent, owner.id) == 0
    assert _count(session, StressEpisode, owner.id) == 0
    assert _count(session, SymptomEvent, owner.id) == 0
    assert _count(session, Medication, owner.id) == 1
    assert _count(session, BloodPressureEvent, owner.id) == 1
    assert _count(session, WeightEvent, owner.id) == 1
    assert _count(session, DiaryEvent, owner.id) == 1
    assert _count(session, GarminMetricEvent, owner.id) == 1
    assert _count(session, LabPanel, owner.id) == 1
    assert _count(session, LabResult, owner.id) == 1
    audit_entry = session.scalar(
        select(AuditEntry).where(AuditEntry.action == AuditAction.SELECTIVE_TEST_DATA_RESET)
    )
    assert audit_entry is not None
    assert audit_entry.change_summary is not None
    assert "synthetic symptom" not in audit_entry.change_summary


def test_wrong_confirmation_changes_nothing(session: Session) -> None:
    owner = _seed_mixed_data(session)
    preview = preview_selective_test_data_reset(session, owner_id=owner.id)
    with pytest.raises(SelectiveTestDataCleanupError, match="confirmation did not match"):
        execute_selective_test_data_reset(
            session,
            owner_id=owner.id,
            preview=preview,
            confirmation="wrong",
        )
    assert _count(session, RegimenVersion, owner.id) == 1
    assert _count(session, DoseEvent, owner.id) == 2
    assert _count(session, BloodPressureEvent, owner.id) == 1


def test_preview_is_invalidated_when_target_changes(session: Session) -> None:
    owner = _seed_mixed_data(session)
    preview = preview_selective_test_data_reset(session, owner_id=owner.id)
    session.add(
        StressEpisode(
            owner_id=owner.id,
            trigger="Late synthetic episode",
            status=EpisodeStatus.OPEN,
            started_at=datetime(2026, 8, 15, 14, tzinfo=UTC),
            timezone=ZONE,
            recorded_at=datetime(2026, 8, 15, 14, tzinfo=UTC),
        )
    )
    session.flush()
    with pytest.raises(SelectiveTestDataCleanupError, match="target changed"):
        execute_selective_test_data_reset(
            session,
            owner_id=owner.id,
            preview=preview,
            confirmation=preview.confirmation_phrase,
        )
    assert _count(session, StressEpisode, owner.id) == 2
