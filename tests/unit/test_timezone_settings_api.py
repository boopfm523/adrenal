"""The timezone settings endpoint: what it reports, and what it refuses to record.

Runs against the SQLite identity stand-in rather than a container, because everything
asserted here is application logic -- resolution, validation, and the shape the web UI
reads. The constraints the ledger rests on are checked in
``tests/integration/test_timezone_stay_schema.py``, where they are real.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session, sessionmaker

from healthcurve.api import deps
from healthcurve.api.routers import timezone as timezone_router
from healthcurve.identity import places
from healthcurve.identity.models import Owner, TimezoneStay, TimezoneStaySource
from tests.fixtures.identity_sqlite import identity_engine

HOME = "Europe/London"


@pytest.fixture
def factory() -> Iterator[sessionmaker[Session]]:
    engine = identity_engine()
    yield sessionmaker(engine, expire_on_commit=False)
    engine.dispose()


@pytest.fixture
def owner(factory: sessionmaker[Session]) -> Owner:
    with factory() as session, session.begin():
        record = Owner(
            id=uuid.uuid4(),
            email="traveller@example.test",
            password_hash="synthetic-not-a-real-hash",  # pragma: allowlist secret
            default_timezone=HOME,
        )
        session.add(record)
    return record


@pytest.fixture
def client(factory: sessionmaker[Session], owner: Owner) -> Iterator[TestClient]:
    app = FastAPI()
    app.include_router(timezone_router.router, prefix="/api/v1")

    def session_override() -> Iterator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        finally:
            session.close()

    app.dependency_overrides[deps.session_scope] = session_override
    app.dependency_overrides[deps.current_owner] = lambda: owner
    # CSRF needs a real login to exercise; it is asserted end to end in
    # tests/integration/test_api_safety.py instead.
    app.dependency_overrides[deps.require_csrf] = lambda: None
    with TestClient(app) as test_client:
        yield test_client


def test_reads_the_home_zone_before_any_travel(client: TestClient) -> None:
    body = client.get("/api/v1/settings/timezone").json()
    assert body["current_timezone"] == HOME
    assert body["home_timezone"] == HOME
    assert body["is_home"] is True
    assert body["stays"] == []


def test_records_a_stay_and_reports_it_as_current(client: TestClient) -> None:
    response = client.post("/api/v1/settings/timezone", json={"timezone": "America/Denver"})
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["recorded"] is True
    assert body["current_timezone"] == "America/Denver"
    # Home does not move. It is where the owner lives, not where they are.
    assert body["home_timezone"] == HOME
    assert body["is_home"] is False
    assert [stay["timezone"] for stay in body["stays"]] == ["America/Denver"]
    assert body["stays"][0]["source"] == TimezoneStaySource.WEB

    assert client.get("/api/v1/settings/timezone").json()["current_timezone"] == "America/Denver"


def test_accepts_a_place_name_the_alias_table_knows(client: TestClient) -> None:
    body = client.post("/api/v1/settings/timezone", json={"timezone": "Denver"}).json()
    assert body["current_timezone"] == "America/Denver"


def test_restating_the_current_zone_writes_no_row(client: TestClient) -> None:
    client.post("/api/v1/settings/timezone", json={"timezone": "America/Denver"})
    again = client.post("/api/v1/settings/timezone", json={"timezone": "America/Denver"})
    assert again.status_code == 200, again.text
    assert again.json()["recorded"] is False
    # One journey, one row: a restatement must not make the ledger unreadable.
    assert len(again.json()["stays"]) == 1


def test_a_label_is_kept_with_the_stay(client: TestClient) -> None:
    body = client.post(
        "/api/v1/settings/timezone", json={"timezone": "America/Denver", "label": "Denver trip"}
    ).json()
    assert body["stays"][0]["label"] == "Denver trip"


def test_an_unknown_place_is_rejected_and_records_nothing(
    client: TestClient, factory: sessionmaker[Session]
) -> None:
    response = client.post("/api/v1/settings/timezone", json={"timezone": "Mars/Olympus"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "invalid_timezone"
    with factory() as session:
        assert session.query(TimezoneStay).count() == 0


def test_an_ambiguous_place_names_the_candidates(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A guess here would record the wrong offset silently, so it asks instead.

    Forced, because today's tz database has no city name the resolver cannot settle on
    its own. The branch has to keep working for the day it does.
    """

    def ambiguous(place: str, *, at: datetime | None = None) -> str | None:
        raise places.AmbiguousPlaceError(place, ["America/Denver", "America/Chicago"])

    monkeypatch.setattr(places, "resolve", ambiguous)
    response = client.post("/api/v1/settings/timezone", json={"timezone": "Somewhere"})
    assert response.status_code == 422
    detail = response.json()["detail"]
    assert detail["code"] == "ambiguous_place"
    assert detail["candidates"] == ["America/Denver", "America/Chicago"]


def test_a_stay_cannot_begin_in_the_future(client: TestClient) -> None:
    ahead = (datetime.now(UTC) + timedelta(hours=2)).isoformat()
    response = client.post(
        "/api/v1/settings/timezone", json={"timezone": "America/Denver", "started_at": ahead}
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "future_started_at"


def test_a_naive_started_at_is_refused(client: TestClient) -> None:
    response = client.post(
        "/api/v1/settings/timezone",
        json={"timezone": "America/Denver", "started_at": "2026-09-20T09:00:00"},
    )
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "naive_started_at"


def test_a_backdated_stay_reports_the_wall_time_where_it_began(client: TestClient) -> None:
    arrival = datetime.now(UTC) - timedelta(days=2)
    body = client.post(
        "/api/v1/settings/timezone",
        json={"timezone": "America/Denver", "started_at": arrival.isoformat()},
    ).json()
    stay = body["stays"][0]
    assert datetime.fromisoformat(stay["started_at"]) == arrival
    # The local reading is the clock the owner actually saw on arrival, not the one
    # at home -- which is the whole point of the ledger.
    expected = arrival.astimezone(ZoneInfo("America/Denver")).replace(tzinfo=None)
    assert datetime.fromisoformat(stay["started_at_local"]) == expected
    assert stay["utc_offset_minutes"] in (-360, -420)


def test_history_is_most_recent_first(client: TestClient) -> None:
    now = datetime.now(UTC)
    client.post(
        "/api/v1/settings/timezone",
        json={"timezone": "America/Denver", "started_at": (now - timedelta(days=3)).isoformat()},
    )
    body = client.post(
        "/api/v1/settings/timezone",
        json={"timezone": "America/Chicago", "started_at": (now - timedelta(days=1)).isoformat()},
    ).json()
    assert [stay["timezone"] for stay in body["stays"]] == ["America/Chicago", "America/Denver"]
    assert body["current_timezone"] == "America/Chicago"
