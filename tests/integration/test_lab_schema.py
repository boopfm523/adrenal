"""PostgreSQL invariants for exact laboratory source facts."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.db import SCHEMAS, Base
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.identity.models import Owner
from healthcurve.labs.models import LabPanel, LabResult
from tests.fixtures.synthetic import SYNTHETIC_MARKER

pytestmark = [pytest.mark.postgres, pytest.mark.slow]


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


def _owner(label: str) -> Owner:
    return Owner(
        id=uuid.uuid4(),
        email=f"{label}@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="America/New_York",
    )


def _panel(owner_id: uuid.UUID) -> LabPanel:
    return LabPanel(
        owner_id=owner_id,
        occurred_at=datetime(2026, 3, 8, 12, 15, tzinfo=UTC),
        local_time=datetime(2026, 3, 8, 8, 15),  # noqa: DTZ001
        timezone="America/New_York",
        utc_offset_minutes=-240,
        reported_at=datetime(2026, 3, 8, 15, 45, tzinfo=UTC),
        reported_local_time=datetime(2026, 3, 8, 11, 45),  # noqa: DTZ001
        reported_timezone="America/New_York",
        reported_utc_offset_minutes=-240,
        source_type=SourceType.WEB,
        confirmation_state=ConfirmationState.DIRECT,
        laboratory_name="Synthetic Reference Laboratory",
        accession_id="SYNTHETIC-ACCESSION",
        specimen_type="Synthetic serum",
        report_status="final",
    )


def test_source_strings_and_temporal_provenance_round_trip_exactly(engine: Engine) -> None:
    owner = _owner("lab-round-trip")
    panel = _panel(owner.id)
    numeric = LabResult(
        owner_id=owner.id,
        panel=panel,
        source_row_index=2,
        analyte_name="Synthetic analyte (original label)",
        original_value="< 5.0",
        original_unit="mg/dL (as reported)",
        original_reference_range="3.1 – 4.9",  # noqa: RUF001 - deliberate source text
        abnormal_flag="H*",
        normalized_analyte_code="synthetic-code",
        normalized_value=Decimal("0.0500000000"),
        normalized_unit="g/L",
        normalization_method="synthetic-map-v1",
    )
    qualitative = LabResult(
        owner_id=owner.id,
        panel=panel,
        analyte_name="Synthetic qualitative analyte",
        qualitative_result="Not detected (lab wording)",
        abnormal_flag=None,
    )
    # A value outside the printed range may still have no lab flag. The application
    # must not synthesize one by comparing strings or derived values.
    unflagged = LabResult(
        owner_id=owner.id,
        panel=panel,
        analyte_name="Synthetic unflagged analyte",
        original_value="99",
        original_unit="units",
        original_reference_range="1-2",
        abnormal_flag=None,
    )
    with Session(engine) as session, session.begin():
        session.add_all((owner, panel, numeric, qualitative, unflagged))
        session.flush()
        panel_id = panel.id

    with Session(engine) as session:
        stored = session.scalar(select(LabPanel).where(LabPanel.id == panel_id))
        assert stored is not None
        assert stored.occurred_at == datetime(2026, 3, 8, 12, 15, tzinfo=UTC)
        assert stored.local_time == datetime(2026, 3, 8, 8, 15)  # noqa: DTZ001
        assert stored.timezone == "America/New_York"
        assert stored.utc_offset_minutes == -240
        assert stored.reported_at == datetime(2026, 3, 8, 15, 45, tzinfo=UTC)
        assert stored.reported_local_time == datetime(2026, 3, 8, 11, 45)  # noqa: DTZ001
        assert stored.reported_timezone == "America/New_York"
        assert stored.reported_utc_offset_minutes == -240
        by_name = {result.analyte_name: result for result in stored.results}
        stored_numeric = by_name["Synthetic analyte (original label)"]
        assert stored_numeric.original_value == "< 5.0"
        assert stored_numeric.original_unit == "mg/dL (as reported)"
        assert stored_numeric.original_reference_range == "3.1 – 4.9"  # noqa: RUF001
        assert stored_numeric.abnormal_flag == "H*"
        assert stored_numeric.normalized_value == Decimal("0.0500000000")
        assert by_name["Synthetic qualitative analyte"].qualitative_result == (
            "Not detected (lab wording)"
        )
        assert by_name["Synthetic unflagged analyte"].abnormal_flag is None


@pytest.mark.parametrize(
    "result",
    [
        LabResult(owner_id=uuid.uuid4(), panel_id=uuid.uuid4(), analyte_name="empty"),
        LabResult(
            owner_id=uuid.uuid4(),
            panel_id=uuid.uuid4(),
            analyte_name="derived without provenance",
            original_value="1",
            normalized_value=Decimal("1"),
        ),
    ],
)
def test_invalid_lab_result_shapes_are_rejected(engine: Engine, result: LabResult) -> None:
    owner = _owner(f"lab-invalid-{uuid.uuid4()}")
    panel = _panel(owner.id)
    result.owner_id = owner.id
    result.panel_id = panel.id
    with Session(engine) as session, pytest.raises(IntegrityError), session.begin():
        session.add_all((owner, panel, result))


def test_result_cannot_reference_another_owners_panel(engine: Engine) -> None:
    first = _owner("lab-owner-one")
    second = _owner("lab-owner-two")
    panel = _panel(first.id)
    crossed = LabResult(
        owner_id=second.id,
        panel_id=panel.id,
        analyte_name="Synthetic crossed owner",
        original_value="1",
    )
    with Session(engine) as session, pytest.raises(IntegrityError), session.begin():
        session.add_all((first, second, panel, crossed))
