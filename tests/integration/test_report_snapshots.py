"""PostgreSQL proof that report snapshots are partitioned and reproducible."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import date
from decimal import Decimal

import pytest
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.db import SCHEMAS, Base
from healthcurve.identity.models import Owner
from healthcurve.reports.models import ReportSnapshot
from healthcurve.reports.service import SnapshotValidationError, create_snapshot, document
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


def owner() -> Owner:
    return Owner(
        id=uuid.uuid4(),
        email="report-owner@example.test",
        password_hash=f"{SYNTHETIC_MARKER}-hash",
        default_timezone="America/New_York",
    )


def partitions(*, ai: object = None) -> dict[str, object]:
    return {
        "fact": [{"id": "fact-1", "amount": "10.0000"}],
        "plan": [{"id": "plan-1", "status": "approved"}],
        "patient_note": [{"id": "note-1", "text": "Synthetic question"}],
        "ai": [] if ai is None else ai,
    }


def manifest(*, ai: list[str] | None = None) -> dict[str, list[str]]:
    return {
        "fact": ["fact-1"],
        "plan": ["plan-1"],
        "patient_note": ["note-1"],
        "ai": [] if ai is None else ai,
    }


def metrics() -> dict[str, object]:
    return {
        "actual_dose_total": {
            "definition": "Sum of current recorded dose facts.",
            "timezone": "America/New_York",
            "value": Decimal("10.0000"),
        }
    }


def test_snapshot_defaults_ai_off_and_round_trips_frozen_canonical_data(engine: Engine) -> None:
    account = owner()
    content = partitions()
    sources = manifest()
    values = metrics()
    with Session(engine) as session, session.begin():
        session.add(account)
        snapshot = create_snapshot(
            session,
            owner_id=account.id,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 9),
            timezone="America/New_York",
            selected_sections=["doses", "patient_questions"],
            source_manifest=sources,
            metric_values=values,
            snapshot_content=content,
        )
        session.flush()
        snapshot_id = snapshot.id
        first_checksum = snapshot.canonical_sha256

    # Mutating the caller's source structures after generation cannot alter the row.
    content["fact"] = []
    sources["fact"].append("later-fact")
    values["actual_dose_total"] = {"definition": "changed", "timezone": "UTC"}

    with Session(engine) as session:
        stored = session.scalar(select(ReportSnapshot).where(ReportSnapshot.id == snapshot_id))
        assert stored is not None
        frozen = document(stored)
        assert stored.include_ai is False
        assert stored.canonical_sha256 == first_checksum
        assert frozen["source_manifest"] == {
            "ai": [],
            "fact": ["fact-1"],
            "patient_note": ["note-1"],
            "plan": ["plan-1"],
        }
        assert frozen["metric_values"]["actual_dose_total"]["value"] == "10.0000"  # type: ignore[index]
        assert frozen["snapshot_content"]["fact"][0]["amount"] == "10.0000"  # type: ignore[index]


def test_same_canonical_input_has_same_checksum_and_ai_requires_opt_in(engine: Engine) -> None:
    account = owner()
    account.email = "report-checksum@example.test"
    account_id = account.id
    with Session(engine) as session, session.begin():
        session.add(account)
        left = create_snapshot(
            session,
            owner_id=account_id,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 9),
            timezone="America/New_York",
            selected_sections=["doses"],
            source_manifest=manifest(),
            metric_values=metrics(),
            snapshot_content=partitions(),
        )
        right = create_snapshot(
            session,
            owner_id=account_id,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 9),
            timezone="America/New_York",
            selected_sections=["doses"],
            source_manifest=manifest(),
            metric_values=metrics(),
            snapshot_content=partitions(),
        )
        session.flush()
        assert left.canonical_sha256 == right.canonical_sha256

    with (
        Session(engine) as session,
        pytest.raises(SnapshotValidationError, match="explicit opt-in"),
    ):
        create_snapshot(
            session,
            owner_id=account_id,
            date_from=date(2026, 8, 1),
            date_to=date(2026, 8, 9),
            timezone="America/New_York",
            selected_sections=["doses"],
            source_manifest=manifest(ai=["ai-1"]),
            metric_values=metrics(),
            snapshot_content=partitions(ai=[{"id": "ai-1", "body": "Synthetic observation"}]),
        )
