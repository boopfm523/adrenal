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
from healthcurve.db import SCHEMAS, Base
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram.handlers import (
    confirm_draft,
    handle_message,
)
from healthcurve.medications.models import DoseEvent, DoseUnit, Medication, Route
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
        assert buttons == ["Confirm", "Edit", "Cancel"]

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
