"""The owner may edit a draft, but editing never records a fact."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.ai.extraction import CandidateType, ValidatedCandidate
from healthcurve.ai.models import DraftState, ExtractionDraft
from healthcurve.context.models import ContextEvent, SavedCoarseLocation
from healthcurve.db import SCHEMAS, Base
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import location
from healthcurve.integrations.telegram.handlers import (
    confirm_draft,
    handle_message,
)
from healthcurve.integrations.telegram.models import TelegramLocationRequest
from healthcurve.medications.models import DoseEvent, DoseUnit, Medication, Route
from healthcurve.vitals.models import BloodPressureEvent, WeightEvent
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

        bp_reply = handle_message(session, owner, text="/bp 40/250 1 08:15", now=NOW)
        assert bp_reply.draft_id is not None
        assert "Blood pressure: 40/250 mmHg, pulse 1 bpm" in bp_reply.text
        assert "Nothing is recorded yet" in bp_reply.text
        assert session.scalar(select(BloodPressureEvent)) is None

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
        assert weight_confirmed.text.startswith("Recorded:")

        kg_reply = handle_message(session, owner, text="/weight 83.1 kg 08:25", now=NOW)
        assert "Weight: 183.2 lb (entered 83.1 kg)" in kg_reply.text
        assert "Nothing is recorded yet" in kg_reply.text


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
