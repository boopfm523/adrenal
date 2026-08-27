"""The owner may edit a draft, but editing never records a fact."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.ai.extraction import CandidateType, FlagCode, ValidatedCandidate
from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.ai.ollama import ModelOutcome, ModelResult, OllamaClient
from healthcurve.context.models import ContextEvent, SavedCoarseLocation
from healthcurve.db import SCHEMAS, Base
from healthcurve.episodes.models import EpisodeStatus, StressEpisode
from healthcurve.events.models import (
    DiaryEvent,
    LifeEvent,
    LifeEventCategory,
    MealEvent,
    MealSize,
    SymptomEvent,
    SymptomTrackingCategory,
)
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import location
from healthcurve.integrations.telegram.handlers import (
    confirm_draft,
    handle_message,
)
from healthcurve.integrations.telegram.models import TelegramLocationRequest
from healthcurve.medications.models import DoseCategory, DoseEvent, DoseUnit, Medication, Route
from healthcurve.vitals.models import (
    BloodPressureEvent,
    BodyPosition,
    MeasurementSetting,
    TemperatureEvent,
    WeightEvent,
    WeightUnit,
)
from tests.fixtures.synthetic import SYNTHETIC_MARKER

pytestmark = [pytest.mark.postgres, pytest.mark.slow]
NOW = datetime(2026, 8, 9, 9, tzinfo=UTC)


@pytest.fixture(scope="module")
def engine() -> Iterator[Engine]:
    with PostgresContainer("postgres:16-alpine", driver="psycopg") as container:
        engine = create_engine(container.get_connection_url())
        with engine.begin() as connection:
            for schema in SCHEMAS:
                connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
            connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
        Base.metadata.create_all(engine)
        yield engine
        engine.dispose()


def test_all_supported_fields_edit_without_bypassing_confirmation(engine: Engine) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email="draft-edit@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    original_medication = Medication(
        owner_id=owner.id,
        name="Synthetic medication alpha",
        normalized_name="synthetic medication alpha",
        default_unit=DoseUnit.MG,
        default_route=Route.ORAL,
    )
    replacement_medication = Medication(
        owner_id=owner.id,
        name="Synthetic medication beta",
        normalized_name="synthetic medication beta",
        default_unit=DoseUnit.MG,
        default_route=Route.ORAL,
    )
    with Session(engine) as session, session.begin():
        session.add_all((owner, original_medication, replacement_medication))
        session.flush()
        initial = ValidatedCandidate(
            type=CandidateType.DOSE,
            medication_id=original_medication.id,
            medication_name=original_medication.name,
            amount=Decimal("10"),
            unit="mg",
            route="oral",
            local_time=datetime(2026, 8, 9, 7),  # noqa: DTZ001
            timezone="UTC",
            confidence=0.9,
        )
        original_dump = initial.model_dump(mode="json")
        draft = ExtractionDraft(
            owner_id=owner.id,
            candidates=[original_dump],
            raw_text=f"{SYNTHETIC_MARKER} original draft",
            source="telegram",
            prompt_version="synthetic-test-v1",
            schema_version="synthetic-test-v1",
        )
        session.add(draft)
        session.flush()

        rejected = handle_message(session, owner, text="/edit 1 amount 999", now=NOW)
        assert "outside the accepted range" in rejected.text
        rejected = handle_message(
            session, owner, text="/edit 1 medication invented medicine", now=NOW
        )
        assert "Choose a medication already in your record" in rejected.text
        assert draft.state is DraftState.PENDING
        assert draft.original_candidates is None
        assert draft.candidates == [original_dump]
        assert session.scalar(select(DoseEvent)) is None

        for command in (
            "/edit 1 amount 15",
            "/edit 1 unit mcg",
            "/edit 1 time 07:05",
            "/edit 1 medication Synthetic medication beta",
            "/edit 1 unit mg",
        ):
            reply = handle_message(session, owner, text=command, now=NOW)
            assert reply.text.startswith("Edited draft:")
            assert session.scalar(select(DoseEvent)) is None

        buttons = [
            button["text"]
            for row in (reply.reply_markup or {})["inline_keyboard"]
            for button in row
        ]
        assert buttons == ["Confirm", "Edit", "Cancel", "Add location (optional)"]

        assert draft.state is DraftState.EDITED
        assert draft.resolved_at is None
        assert draft.original_candidates == [original_dump]
        edited = ValidatedCandidate.model_validate(draft.candidates[0])
        assert edited.amount == Decimal("15")
        assert edited.unit == "mg"
        assert edited.local_time == datetime(2026, 8, 9, 7, 5)  # noqa: DTZ001
        assert edited.medication_id == replacement_medication.id

        reply = confirm_draft(session, owner, draft.id)
        session.flush()
        recorded = session.scalar(select(DoseEvent))
        assert reply.text.startswith("Recorded:")
        assert recorded is not None
        assert recorded.amount == Decimal("15")
        assert recorded.medication_id == replacement_medication.id
        assert draft.state is DraftState.EDITED
        assert draft.resolved_at is not None
        assert draft.raw_text is None
        assert not draft.is_pending


def test_vital_commands_remain_drafts_until_confirmed_and_support_safe_edits(
    engine: Engine,
) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email="vital-draft@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
        session.flush()

        bp_reply = handle_message(session, owner, text="/bp 40/250 1 standing 08:15", now=NOW)
        assert bp_reply.draft_id is not None
        assert "Blood pressure: 40/250 mmHg, pulse 1 bpm" in bp_reply.text
        assert "Nothing is recorded yet" in bp_reply.text
        assert session.scalar(select(BloodPressureEvent)) is None
        bp_edited = handle_message(session, owner, text="/edit 1 position sitting", now=NOW)
        assert bp_edited.text.startswith("Edited draft:")

        bp_confirmed = confirm_draft(session, owner, bp_reply.draft_id)
        session.flush()
        pressure = session.scalar(
            select(BloodPressureEvent).where(BloodPressureEvent.owner_id == owner.id)
        )
        assert pressure is not None
        assert (pressure.systolic_mmhg, pressure.diastolic_mmhg, pressure.pulse_bpm) == (
            40,
            250,
            1,
        )
        assert pressure.confirmation_state.value == "confirmed_from_draft"
        assert pressure.measurement_setting.value == "home"
        assert pressure.body_position is BodyPosition.SITTING
        assert bp_confirmed.text.startswith("Recorded:")

        weight_reply = handle_message(session, owner, text="/weight 180 lb 08:20", now=NOW)
        assert weight_reply.draft_id is not None
        assert session.scalar(select(WeightEvent)) is None
        edited = handle_message(session, owner, text="/edit 1 amount 181", now=NOW)
        assert edited.text.startswith("Edited draft:")
        assert session.scalar(select(WeightEvent)) is None

        weight_confirmed = confirm_draft(session, owner, weight_reply.draft_id)
        session.flush()
        weight = session.scalar(select(WeightEvent).where(WeightEvent.owner_id == owner.id))
        assert weight is not None
        assert weight.value == Decimal("181.0000")
        assert weight.unit.value == "lb"
        assert weight.normalized_kg == Decimal("82.1002")
        assert weight.confirmation_state.value == "confirmed_from_draft"
        assert weight.measurement_setting.value == "home"

        provider_candidate = ValidatedCandidate(
            type=CandidateType.WEIGHT,
            weight_value=Decimal("182"),
            weight_unit=WeightUnit.LB,
            measurement_setting=MeasurementSetting.PROVIDER,
            local_time=datetime(2026, 8, 9, 8, 25),  # noqa: DTZ001
            timezone="UTC",
            confidence=0.99,
        )
        provider_draft = ExtractionDraft(
            owner_id=owner.id,
            candidates=[provider_candidate.model_dump(mode="json")],
            raw_text=f"{SYNTHETIC_MARKER} weight at doctor's office",
            source="telegram",
            prompt_version="synthetic-test-v1",
            schema_version="synthetic-test-v1",
        )
        session.add(provider_draft)
        session.flush()
        confirm_draft(session, owner, provider_draft.id)
        session.flush()
        provider_weight = session.scalar(
            select(WeightEvent).where(
                WeightEvent.owner_id == owner.id,
                WeightEvent.measurement_setting == MeasurementSetting.PROVIDER,
            )
        )
        assert provider_weight is not None
        assert weight_confirmed.text.startswith("Recorded:")

        kg_reply = handle_message(session, owner, text="/weight 83.1 kg 08:25", now=NOW)
        assert "Weight: 183.2 lb (entered 83.1 kg)" in kg_reply.text
        assert "Nothing is recorded yet" in kg_reply.text

        temperature_reply = handle_message(session, owner, text="/temperature 38 C 08:30", now=NOW)
        assert temperature_reply.draft_id is not None
        assert "Temperature: 100.4 °F (38.0 °C)" in temperature_reply.text
        assert (
            session.scalar(select(TemperatureEvent).where(TemperatureEvent.owner_id == owner.id))
            is None
        )
        temperature_confirmed = confirm_draft(session, owner, temperature_reply.draft_id)
        session.flush()
        temperature = session.scalar(
            select(TemperatureEvent).where(TemperatureEvent.owner_id == owner.id)
        )
        assert temperature is not None
        assert temperature.value == Decimal("38.00")
        assert temperature.unit.value == "c"
        assert temperature.normalized_c == Decimal("38.00")
        assert temperature.confirmation_state.value == "confirmed_from_draft"
        assert temperature_confirmed.text.startswith("Recorded:")

        inferred_reply = handle_message(session, owner, text="/temperature 98.6 08:31", now=NOW)
        assert inferred_reply.draft_id is not None
        assert "Temperature: 98.6 °F (37.0 °C) · F inferred from value" in inferred_reply.text
        assert "you omitted the temperature unit" in inferred_reply.text

        invalid_reply = handle_message(session, owner, text="/temperature 60", now=NOW)
        assert invalid_reply.draft_id is None
        assert "between 25 and 45 °C (77 and 113 °F)" in invalid_reply.text


def test_simple_natural_language_fast_path_still_requires_confirmation(engine: Engine) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email=f"fast-path-{uuid.uuid4()}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    medication = Medication(
        owner_id=owner.id,
        name="Synthetic hydrocortisone",
        normalized_name="synthetic hydrocortisone",
        default_unit=DoseUnit.MG,
        default_route=Route.ORAL,
    )
    client = MagicMock(spec=OllamaClient)

    with Session(engine) as session, session.begin():
        session.add_all((owner, medication))
        session.flush()

        temperature_reply = handle_message(
            session,
            owner,
            text="My temperature was 98.6 at 08:31",
            message_id="synthetic-fast-temperature",
            client=client,
            now=NOW,
        )
        assert temperature_reply.draft_id is not None
        assert (
            session.scalar(select(TemperatureEvent).where(TemperatureEvent.owner_id == owner.id))
            is None
        )
        temperature_draft = session.get(ExtractionDraft, temperature_reply.draft_id)
        assert temperature_draft is not None
        assert temperature_draft.source == "telegram_fast"
        assert temperature_draft.model_name is None
        assert temperature_draft.raw_text == "My temperature was 98.6 at 08:31"

        confirm_draft(session, owner, temperature_reply.draft_id)
        session.flush()
        temperature = session.scalar(
            select(TemperatureEvent).where(TemperatureEvent.owner_id == owner.id)
        )
        assert temperature is not None
        assert temperature.value == Decimal("98.60")
        assert temperature.unit.value == "f"
        assert temperature.confirmation_state.value == "confirmed_from_draft"
        assert temperature_draft.raw_text is None

        dose_reply = handle_message(
            session,
            owner,
            text="I took 5 mg synthetic hydrocortisone at 08:32",
            message_id="synthetic-fast-dose",
            client=client,
            now=NOW,
        )
        assert dose_reply.draft_id is not None
        assert session.scalar(select(DoseEvent).where(DoseEvent.owner_id == owner.id)) is None

        confirm_draft(session, owner, dose_reply.draft_id)
        session.flush()
        dose = session.scalar(select(DoseEvent).where(DoseEvent.owner_id == owner.id))
        assert dose is not None
        assert dose.amount == Decimal("5.0000")
        assert dose.confirmation_state.value == "confirmed_from_draft"
        client.generate_json.assert_not_called()


def test_explicit_symptom_category_is_confirmed_and_editable(engine: Engine) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email=f"symptom-category-{uuid.uuid4()}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
        session.flush()

        reply = handle_message(
            session,
            owner,
            text="/symptom dizziness 4 category=postural",
            now=NOW,
        )
        assert reply.draft_id is not None
        assert "category postural" in reply.text
        assert session.scalar(select(SymptomEvent)) is None

        edited = handle_message(
            session,
            owner,
            text="/edit 1 category mineralocorticoid",
            now=NOW,
        )
        assert edited.text.startswith("Edited draft:")
        confirm_draft(session, owner, reply.draft_id)
        session.flush()

        symptom = session.scalar(select(SymptomEvent).where(SymptomEvent.owner_id == owner.id))
        assert symptom is not None
        assert symptom.tracking_category is SymptomTrackingCategory.MINERALOCORTICOID
        assert symptom.tracking_category_revision == "symptom-tracking-category-v1"


@pytest.mark.parametrize(
    ("message", "expected_value", "expected_unit"),
    [
        ("/weight 180.9 lbs", Decimal("180.9000"), WeightUnit.LB),
        ("/weight 82.1 kgs", Decimal("82.1000"), WeightUnit.KG),
        ("Add a weight of 179.6 lbs", Decimal("179.6000"), WeightUnit.LB),
        ("Add a body weight of 81.5 kgs.", Decimal("81.5000"), WeightUnit.KG),
    ],
)
def test_plural_and_conversational_weight_entries_create_confirmable_drafts(
    engine: Engine, message: str, expected_value: Decimal, expected_unit: WeightUnit
) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email=f"weight-{uuid.uuid4()}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
        session.flush()

        reply = handle_message(session, owner, text=message, now=NOW)

        assert reply.draft_id is not None
        assert "Nothing is recorded yet" in reply.text
        assert session.scalar(select(WeightEvent).where(WeightEvent.owner_id == owner.id)) is None
        draft = session.get(ExtractionDraft, reply.draft_id)
        assert draft is not None
        candidate = ValidatedCandidate.model_validate(draft.candidates[0])
        assert candidate.weight_value == expected_value
        assert candidate.weight_unit is expected_unit


@pytest.mark.parametrize(
    ("message", "expected_size", "expected_time", "assumed_time"),
    [
        (
            "I just had a meal",
            None,
            datetime(2026, 8, 9, 9, 0),  # noqa: DTZ001
            True,
        ),
        (
            "I had a large meal at 12:30",
            MealSize.L,
            datetime(2026, 8, 8, 12, 30),  # noqa: DTZ001
            False,
        ),
        (
            "/meal XXL 18:45",
            MealSize.XXL,
            datetime(2026, 8, 8, 18, 45),  # noqa: DTZ001
            False,
        ),
        (
            "/meal L",
            MealSize.L,
            datetime(2026, 8, 9, 9, 0),  # noqa: DTZ001
            True,
        ),
        (
            "I had a small breakfast at 8.15 am",
            MealSize.S,
            datetime(2026, 8, 9, 8, 15),  # noqa: DTZ001
            False,
        ),
        (
            "Had an extra small meal.",
            MealSize.XS,
            datetime(2026, 8, 9, 9, 0),  # noqa: DTZ001
            True,
        ),
    ],
)
def test_meal_messages_create_confirmable_observed_facts_without_inventing_size(
    engine: Engine,
    message: str,
    expected_size: MealSize | None,
    expected_time: datetime,
    assumed_time: bool,
) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email=f"meal-{uuid.uuid4()}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
        session.flush()

        reply = handle_message(session, owner, text=message, now=NOW)

        assert reply.draft_id is not None
        assert "Nothing is recorded yet" in reply.text
        assert session.scalar(select(MealEvent).where(MealEvent.owner_id == owner.id)) is None
        draft = session.get(ExtractionDraft, reply.draft_id)
        assert draft is not None
        candidate = ValidatedCandidate.model_validate(draft.candidates[0])
        assert candidate.type is CandidateType.MEAL
        assert candidate.meal_size is expected_size
        assert candidate.local_time == expected_time
        assert (FlagCode.ASSUMED_TIME in candidate.flags) is assumed_time

        confirm_draft(session, owner, reply.draft_id)
        session.flush()
        recorded = session.scalar(select(MealEvent).where(MealEvent.owner_id == owner.id))
        assert recorded is not None
        assert recorded.size is expected_size
        assert recorded.local_time == expected_time
        assert recorded.confirmation_state.value == "confirmed_from_draft"


def test_diary_and_life_event_commands_confirm_into_separate_fact_tables(engine: Engine) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email=f"context-entry-{uuid.uuid4()}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
        session.flush()

        diary_reply = handle_message(
            session,
            owner,
            text="/diary Synthetic private sleep note --time=07:30 --sensitive",
            now=NOW,
        )
        assert diary_reply.draft_id is not None
        assert "Diary: Synthetic private sleep note · sensitive at 07:30" in diary_reply.text
        assert session.scalar(select(DiaryEvent).where(DiaryEvent.owner_id == owner.id)) is None
        diary_draft = session.get(ExtractionDraft, diary_reply.draft_id)
        assert diary_draft is not None
        diary_candidate = ValidatedCandidate.model_validate(diary_draft.candidates[0])
        assert diary_candidate.type is CandidateType.DIARY
        assert diary_candidate.is_sensitive is True

        confirm_draft(session, owner, diary_reply.draft_id)
        session.flush()
        diary = session.scalar(select(DiaryEvent).where(DiaryEvent.owner_id == owner.id))
        assert diary is not None
        assert diary.text == "Synthetic private sleep note"
        assert diary.is_sensitive is True

        life_reply = handle_message(
            session,
            owner,
            text="/lifeevent travel Synthetic overnight flight --time=08:15 --sensitive",
            now=NOW,
        )
        assert life_reply.draft_id is not None
        assert (
            "Life event (travel): Synthetic overnight flight · sensitive at 08:15"
            in life_reply.text
        )
        assert session.scalar(select(LifeEvent).where(LifeEvent.owner_id == owner.id)) is None
        life_draft = session.get(ExtractionDraft, life_reply.draft_id)
        assert life_draft is not None
        life_candidate = ValidatedCandidate.model_validate(life_draft.candidates[0])
        assert life_candidate.type is CandidateType.LIFE_EVENT
        assert life_candidate.life_event_category is LifeEventCategory.TRAVEL
        assert life_candidate.is_sensitive is True

        confirm_draft(session, owner, life_reply.draft_id)
        session.flush()
        life_event = session.scalar(select(LifeEvent).where(LifeEvent.owner_id == owner.id))
        diary_count = len(
            session.scalars(select(DiaryEvent).where(DiaryEvent.owner_id == owner.id)).all()
        )
        assert life_event is not None
        assert life_event.title == "Synthetic overnight flight"
        assert life_event.category is LifeEventCategory.TRAVEL
        assert life_event.is_sensitive is True
        assert life_event.confirmation_state.value == "confirmed_from_draft"
        assert diary_count == 1


def test_conversational_episode_shortcuts_allow_an_unspecified_trigger(engine: Engine) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email=f"episode-{uuid.uuid4()}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
        session.flush()

        opened = handle_message(session, owner, text="episode starting", now=NOW)
        episode = session.scalar(select(StressEpisode).where(StressEpisode.owner_id == owner.id))
        assert opened.text == "Episode opened: unspecified. Doses you log now will be linked to it."
        assert episode is not None and episode.status is EpisodeStatus.OPEN

        closed = handle_message(
            session, owner, text="the episode is over", now=NOW + timedelta(hours=1)
        )
        assert closed.text.startswith("Episode closed after about 1 hour(s)")
        assert episode.status is EpisodeStatus.RESOLVED


@pytest.mark.parametrize(
    ("message", "expected_name", "expected_time", "assumed_time"),
    [
        (
            "I just had a symptom of dizziness at 14:30",
            "dizziness",
            datetime(2026, 8, 8, 14, 30),  # noqa: DTZ001
            False,
        ),
        (
            "I feel a symptom of nausea",
            "nausea",
            datetime(2026, 8, 9, 9, 0),  # noqa: DTZ001
            True,
        ),
    ],
)
def test_conversational_symptoms_create_confirmable_drafts_with_visible_time_basis(
    engine: Engine,
    message: str,
    expected_name: str,
    expected_time: datetime,
    assumed_time: bool,
) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email=f"symptom-{uuid.uuid4()}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
        session.flush()

        reply = handle_message(session, owner, text=message, now=NOW)

        assert reply.draft_id is not None
        assert "Nothing is recorded yet" in reply.text
        draft = session.get(ExtractionDraft, reply.draft_id)
        assert draft is not None
        candidate = ValidatedCandidate.model_validate(draft.candidates[0])
        assert candidate.type is CandidateType.SYMPTOM
        assert candidate.symptom_name == expected_name
        assert candidate.local_time == expected_time
        explanation = "you didn't give a time, so I've used when you sent this"
        assert (explanation in reply.text) is assumed_time


def test_ambiguous_conversational_phrase_does_not_start_an_episode(engine: Engine) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email=f"ambiguous-episode-{uuid.uuid4()}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
        session.flush()

        client = MagicMock(spec=OllamaClient)
        client.generate_json.return_value = ModelResult(outcome=ModelOutcome.UNAVAILABLE)
        reply = handle_message(
            session, owner, text="I might have an episode", client=client, now=NOW
        )

        assert "Nothing was recorded" in reply.text
        assert (
            session.scalar(select(StressEpisode).where(StressEpisode.owner_id == owner.id)) is None
        )


def test_open_episode_links_dose_without_silently_reclassifying_it(engine: Engine) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email="dose-category@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="UTC",
    )
    medication = Medication(
        owner_id=owner.id,
        name="Synthetic hydrocortisone",
        normalized_name="synthetic hydrocortisone",
        default_unit=DoseUnit.MG,
        default_route=Route.ORAL,
    )
    with Session(engine) as session, session.begin():
        session.add_all((owner, medication))
        session.flush()
        episode = StressEpisode(
            owner_id=owner.id,
            trigger="Synthetic stress context",
            status=EpisodeStatus.OPEN,
            started_at=NOW,
            timezone="UTC",
            recorded_at=NOW,
        )
        session.add(episode)
        session.flush()

        ordinary = handle_message(
            session, owner, text="/dose 10 Synthetic hydrocortisone 09:00", now=NOW
        )
        assert "Regular dose:" in ordinary.text
        assert ordinary.draft_id is not None
        ordinary_recorded = confirm_draft(session, owner, ordinary.draft_id)
        assert ordinary_recorded.text.startswith("Recorded:")
        first = session.scalar(select(DoseEvent).where(DoseEvent.owner_id == owner.id))
        assert first is not None
        assert first.category is DoseCategory.SCHEDULED
        assert first.episode_id == episode.id

        stress = handle_message(
            session, owner, text="/dose 5 Synthetic hydrocortisone 09:00", now=NOW
        )
        edited = handle_message(session, owner, text="/edit 1 category stress dose", now=NOW)
        assert "Stress dose:" in edited.text
        assert stress.draft_id is not None
        confirm_draft(session, owner, stress.draft_id)
        categories = list(
            session.scalars(
                select(DoseEvent.category)
                .where(DoseEvent.owner_id == owner.id)
                .order_by(DoseEvent.recorded_at, DoseEvent.id)
            )
        )
        assert DoseCategory.SCHEDULED in categories
        assert DoseCategory.STRESS in categories


def test_phone_location_is_rounded_linked_and_consumed_with_draft(engine: Engine) -> None:
    owner = Owner(
        id=uuid.uuid4(),
        email="location-draft@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="America/New_York",
    )
    candidate = ValidatedCandidate(
        type=CandidateType.DIARY,
        text=f"{SYNTHETIC_MARKER} travel note",
        local_time=datetime(2026, 8, 9, 11),  # noqa: DTZ001
        timezone="America/New_York",
        confidence=1.0,
    )
    with Session(engine) as session, session.begin():
        location_now = datetime.now(UTC)
        session.add(owner)
        draft = ExtractionDraft(
            owner_id=owner.id,
            candidates=[candidate.model_dump(mode="json")],
            raw_text=f"{SYNTHETIC_MARKER} travel note",
            source="telegram",
            prompt_version="synthetic-test-v1",
            schema_version="synthetic-test-v1",
        )
        session.add(draft)
        session.flush()

        request = location.begin_request(
            session, owner, chat_id=4242, draft_id=draft.id, now=location_now
        )
        assert request is not None
        assert (
            location.attach_phone_location(
                session,
                owner,
                chat_id=4242,
                latitude=40.71281,
                longitude=-74.00601,
                now=location_now,
            )
            is location.LocationResult.ATTACHED
        )
        assert location.save_attached_as_home(session, owner, draft_id=draft.id)

        reply = confirm_draft(session, owner, draft.id)
        session.flush()

        context = session.scalar(select(ContextEvent).where(ContextEvent.owner_id == owner.id))
        stored_request = session.scalar(
            select(TelegramLocationRequest).where(TelegramLocationRequest.owner_id == owner.id)
        )
        home = session.scalar(
            select(SavedCoarseLocation).where(SavedCoarseLocation.owner_id == owner.id)
        )
        assert "Coarse location context" in reply.text
        assert context is not None
        assert context.latitude == Decimal("40.700000")
        assert context.longitude == Decimal("-74.000000")
        assert context.exact_location_consent is False
        assert stored_request is not None
        assert stored_request.state.value == "used"
        assert stored_request.rounded_latitude is None
        assert home is not None and home.latitude == Decimal("40.7")
