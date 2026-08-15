"""Preview-first removal of owner-declared synthetic test domains.

This is an operator maintenance workflow, not a heuristic classifier.  The owner has
explicitly declared every regimen, recorded dose, stress episode, and symptom to be
test data.  No row is selected by names, dates, notes, or inferred medical content.
The fixed model allow-list below is therefore the safety boundary.
"""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.sql.elements import ColumnElement

from healthcurve.episodes.models import EmergencyInjectionEvent, StressEpisode
from healthcurve.events.base import EventMixin
from healthcurve.events.models import DiaryEvent, LifeEvent, SymptomEvent
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminMetricEvent,
    GarminSleepEvent,
    WearableDailySummary,
)
from healthcurve.integrations.telegram.models import TelegramDoseReminder
from healthcurve.labs.models import LabDocument, LabPanel, LabResult
from healthcurve.medications.models import (
    ApprovedInstruction,
    DoseEvent,
    Medication,
    RegimenDoseSlot,
    RegimenVersion,
)
from healthcurve.operations import audit
from healthcurve.vitals.models import BloodPressureEvent, TemperatureEvent, WeightEvent


class SelectiveTestDataCleanupError(RuntimeError):
    """The selected reset could not be proven safe, so nothing should commit."""


@dataclass(frozen=True, slots=True)
class ResetCounts:
    regimen_versions: int
    regimen_dose_slots: int
    approved_instructions: int
    dose_reminders: int
    dose_events: int
    stress_episodes: int
    symptom_events: int

    @property
    def total(self) -> int:
        return sum(
            (
                self.regimen_versions,
                self.regimen_dose_slots,
                self.approved_instructions,
                self.dose_reminders,
                self.dose_events,
                self.stress_episodes,
                self.symptom_events,
            )
        )


@dataclass(frozen=True, slots=True)
class PreservedCounts:
    medications: int
    emergency_injections: int
    diary_events: int
    life_events: int
    blood_pressure_events: int
    weight_events: int
    temperature_events: int
    garmin_metric_events: int
    garmin_sleep_events: int
    garmin_activity_events: int
    garmin_daily_summaries: int
    lab_documents: int
    lab_panels: int
    lab_results: int


@dataclass(frozen=True, slots=True)
class BlockingReferences:
    emergency_injections_linked_to_episodes: int

    @property
    def total(self) -> int:
        return self.emergency_injections_linked_to_episodes


@dataclass(frozen=True, slots=True)
class SelectiveResetPreview:
    regimen_version_ids: tuple[uuid.UUID, ...]
    regimen_dose_slot_ids: tuple[uuid.UUID, ...]
    approved_instruction_ids: tuple[uuid.UUID, ...]
    dose_reminder_ids: tuple[uuid.UUID, ...]
    dose_event_ids: tuple[uuid.UUID, ...]
    stress_episode_ids: tuple[uuid.UUID, ...]
    symptom_event_ids: tuple[uuid.UUID, ...]
    counts: ResetCounts
    preserved: PreservedCounts
    blockers: BlockingReferences
    confirmation_phrase: str

    @property
    def target_identity(self) -> tuple[tuple[uuid.UUID, ...], ...]:
        """The exact destructive target, excluding independently changing real data."""
        return (
            self.regimen_version_ids,
            self.regimen_dose_slot_ids,
            self.approved_instruction_ids,
            self.dose_reminder_ids,
            self.dose_event_ids,
            self.stress_episode_ids,
            self.symptom_event_ids,
        )


def preview_selective_test_data_reset(
    session: Session, *, owner_id: uuid.UUID
) -> SelectiveResetPreview:
    """Lock and enumerate the exact owner-scoped reset target without changing it."""
    regimens = tuple(
        session.scalars(
            select(RegimenVersion)
            .where(RegimenVersion.owner_id == owner_id)
            .order_by(RegimenVersion.id)
            .with_for_update(of=RegimenVersion)
        ).unique()
    )
    regimen_ids = tuple(row.id for row in regimens)
    slots = tuple(
        session.scalars(
            select(RegimenDoseSlot)
            .join(RegimenVersion, RegimenVersion.id == RegimenDoseSlot.regimen_version_id)
            .where(RegimenVersion.owner_id == owner_id)
            .order_by(RegimenDoseSlot.id)
            .with_for_update(of=RegimenDoseSlot)
        )
    )
    instructions = tuple(
        session.scalars(
            select(ApprovedInstruction)
            .join(RegimenVersion, RegimenVersion.id == ApprovedInstruction.regimen_version_id)
            .where(RegimenVersion.owner_id == owner_id)
            .order_by(ApprovedInstruction.id)
            .with_for_update(of=ApprovedInstruction)
        )
    )
    reminders = tuple(
        session.scalars(
            select(TelegramDoseReminder)
            .where(TelegramDoseReminder.owner_id == owner_id)
            .order_by(TelegramDoseReminder.id)
            .with_for_update(of=TelegramDoseReminder)
        )
    )
    doses = tuple(
        session.scalars(
            select(DoseEvent)
            .where(DoseEvent.owner_id == owner_id)
            .order_by(DoseEvent.id)
            .with_for_update(of=DoseEvent)
        )
    )
    episodes = tuple(
        session.scalars(
            select(StressEpisode)
            .where(StressEpisode.owner_id == owner_id)
            .order_by(StressEpisode.id)
            .with_for_update(of=StressEpisode)
        )
    )
    symptoms = tuple(
        session.scalars(
            select(SymptomEvent)
            .where(SymptomEvent.owner_id == owner_id)
            .order_by(SymptomEvent.id)
            .with_for_update(of=SymptomEvent)
        )
    )
    episode_ids = tuple(row.id for row in episodes)
    linked_injections = _count(
        session,
        EmergencyInjectionEvent,
        owner_id,
        EmergencyInjectionEvent.episode_id.in_(episode_ids) if episode_ids else None,
    )

    target_identity = (
        regimen_ids,
        tuple(row.id for row in slots),
        tuple(row.id for row in instructions),
        tuple(row.id for row in reminders),
        tuple(row.id for row in doses),
        episode_ids,
        tuple(row.id for row in symptoms),
    )
    token_material = "|".join(
        (str(owner_id), *(str(row_id) for group in target_identity for row_id in group))
    ).encode()
    token = hashlib.sha256(token_material).hexdigest()[:12].upper()
    return SelectiveResetPreview(
        regimen_version_ids=target_identity[0],
        regimen_dose_slot_ids=target_identity[1],
        approved_instruction_ids=target_identity[2],
        dose_reminder_ids=target_identity[3],
        dose_event_ids=target_identity[4],
        stress_episode_ids=target_identity[5],
        symptom_event_ids=target_identity[6],
        counts=ResetCounts(
            regimen_versions=len(regimens),
            regimen_dose_slots=len(slots),
            approved_instructions=len(instructions),
            dose_reminders=len(reminders),
            dose_events=len(doses),
            stress_episodes=len(episodes),
            symptom_events=len(symptoms),
        ),
        preserved=_preserved_counts(session, owner_id),
        blockers=BlockingReferences(linked_injections),
        confirmation_phrase=f"CLEAR DECLARED TEST DATA {token}",
    )


def execute_selective_test_data_reset(
    session: Session,
    *,
    owner_id: uuid.UUID,
    preview: SelectiveResetPreview,
    confirmation: str,
) -> ResetCounts:
    """Delete only the preview-bound test domains in the caller's transaction."""
    current = preview_selective_test_data_reset(session, owner_id=owner_id)
    if current.target_identity != preview.target_identity:
        raise SelectiveTestDataCleanupError(
            "the reset target changed after preview; run a new preview; nothing changed"
        )
    if current.blockers.total:
        raise SelectiveTestDataCleanupError(
            "an emergency injection is linked to a target episode; nothing changed"
        )
    if confirmation.strip() != current.confirmation_phrase:
        raise SelectiveTestDataCleanupError("confirmation did not match; nothing changed")
    if current.counts.total == 0:
        raise SelectiveTestDataCleanupError("there is no declared test data to clear")

    try:
        with session.begin_nested():
            _delete_event_chains(session, DoseEvent, owner_id)
            _delete_event_chains(session, SymptomEvent, owner_id)

            for version_id in current.regimen_version_ids:
                version = session.get(RegimenVersion, version_id)
                if version is None:
                    raise SelectiveTestDataCleanupError(
                        "the reset target changed after preview; nothing changed"
                    )
                session.delete(version)
            session.flush()

            for episode_id in current.stress_episode_ids:
                episode = session.get(StressEpisode, episode_id)
                if episode is None:
                    raise SelectiveTestDataCleanupError(
                        "the reset target changed after preview; nothing changed"
                    )
                session.delete(episode)
            session.flush()

            audit.record(
                session,
                actor=audit.actor_for_owner(owner_id),
                action=audit.AuditAction.SELECTIVE_TEST_DATA_RESET,
                target_type="owner_declared_test_data",
                change_summary=(
                    "selective reset; "
                    f"regimen_versions={current.counts.regimen_versions}; "
                    f"slots={current.counts.regimen_dose_slots}; "
                    f"instructions={current.counts.approved_instructions}; "
                    f"dose_reminders={current.counts.dose_reminders}; "
                    f"doses={current.counts.dose_events}; "
                    f"episodes={current.counts.stress_episodes}; "
                    f"symptoms={current.counts.symptom_events}"
                ),
            )
            session.flush()
    except IntegrityError as exc:
        raise SelectiveTestDataCleanupError(
            "an unexpected retained reference prevented the reset; nothing changed"
        ) from exc
    return current.counts


def _delete_event_chains(session: Session, model: type[EventMixin], owner_id: uuid.UUID) -> int:
    """Delete complete correction chains from current leaves to original roots."""
    rows = list(
        session.scalars(select(model).where(model.owner_id == owner_id).with_for_update(of=model))
    )
    remaining = {row.id: row for row in rows}
    while remaining:
        superseded_ids = {
            row.supersedes_id for row in remaining.values() if row.supersedes_id is not None
        }
        leaves = [row for row in remaining.values() if row.id not in superseded_ids]
        if not leaves:
            raise SelectiveTestDataCleanupError(
                "correction history is cyclic and cannot be safely cleared"
            )
        for row in leaves:
            session.delete(row)
            remaining.pop(row.id)
        session.flush()
    return len(rows)


def _preserved_counts(session: Session, owner_id: uuid.UUID) -> PreservedCounts:
    return PreservedCounts(
        medications=_count(session, Medication, owner_id),
        emergency_injections=_count(session, EmergencyInjectionEvent, owner_id),
        diary_events=_count(session, DiaryEvent, owner_id),
        life_events=_count(session, LifeEvent, owner_id),
        blood_pressure_events=_count(session, BloodPressureEvent, owner_id),
        weight_events=_count(session, WeightEvent, owner_id),
        temperature_events=_count(session, TemperatureEvent, owner_id),
        garmin_metric_events=_count(session, GarminMetricEvent, owner_id),
        garmin_sleep_events=_count(session, GarminSleepEvent, owner_id),
        garmin_activity_events=_count(session, GarminActivityEvent, owner_id),
        garmin_daily_summaries=_count(session, WearableDailySummary, owner_id),
        lab_documents=_count(session, LabDocument, owner_id),
        lab_panels=_count(session, LabPanel, owner_id),
        lab_results=_count(session, LabResult, owner_id),
    )


def _count(
    session: Session,
    model: type,
    owner_id: uuid.UUID,
    extra_condition: ColumnElement[bool] | None = None,
) -> int:
    statement = select(func.count()).select_from(model).where(model.owner_id == owner_id)
    if extra_condition is not None:
        statement = statement.where(extra_condition)
    return session.scalar(statement) or 0
