"""End-to-end safety behaviour of the API.

Runs against real PostgreSQL with the real migrations, because most of what is asserted
here is only true if the database constraints exist.
"""

from __future__ import annotations

import os
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, text
from testcontainers.community.postgres import PostgresContainer

from healthcurve.config import Settings, get_settings
from healthcurve.identity import service as auth
from healthcurve.identity.models import Owner

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

REPO_ROOT = Path(__file__).resolve().parents[2]
PASSWORD = "correct-horse-battery-staple"
EMAIL = "owner@example.com"


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    container = PostgresContainer(
        "postgres:16-alpine",
        username="healthcurve",
        password="test-password",
        dbname="healthcurve",
        driver="psycopg",
    )
    with container as running:
        yield running


@pytest.fixture(scope="module")
def settings(postgres: PostgresContainer) -> Settings:
    return Settings(
        # Never read the developer's .env: a real HC_OLLAMA_BASE_URL once made this
        # fixture fail startup validation for reasons unrelated to the test.
        _env_file=None,  # type: ignore[call-arg]
        database_url=postgres.get_connection_url(),
        ollama_base_url="http://ollama:11434",
    )


@pytest.fixture(scope="module")
def engine(settings: Settings, postgres: PostgresContainer) -> Iterator[Engine]:
    eng = create_engine(settings.database_url)
    with eng.begin() as conn:
        for schema in ("fact", "plan", "ai", "ops", "identity"):
            conn.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        conn.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))

    # Apply the real migrations in-process, so this exercises what actually ships
    # without depending on a subprocess inheriting the right environment.
    alembic_config = Config(str(REPO_ROOT / "alembic.ini"))
    alembic_config.set_main_option("script_location", str(REPO_ROOT / "migrations"))
    with mock.patch.dict(os.environ, {"HC_DATABASE_URL": settings.database_url}):
        get_settings.cache_clear()
        command.upgrade(alembic_config, "head")
    get_settings.cache_clear()

    yield eng
    eng.dispose()


@pytest.fixture(scope="module")
def client(settings: Settings, engine: Engine) -> Iterator[TestClient]:
    from sqlalchemy.orm import sessionmaker

    from healthcurve.api import deps
    from healthcurve.app import create_app

    factory = sessionmaker(engine, expire_on_commit=False)

    with factory() as session, session.begin():
        session.add(
            Owner(
                email=EMAIL,
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="Europe/London",
            )
        )

    def override() -> Iterator[Any]:
        db = factory()
        try:
            yield db
            db.commit()
        except Exception:
            db.rollback()
            raise
        finally:
            db.close()

    app = create_app(settings)
    app.dependency_overrides[deps.session_scope] = override
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def logged_in(client: TestClient) -> dict[str, str]:
    response = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": PASSWORD})
    assert response.status_code == 200, response.text
    return {auth.CSRF_HEADER_NAME: response.json()["csrf_token"]}


# ---------------------------------------------------------------------------
# Authentication and CSRF
# ---------------------------------------------------------------------------


def test_health_data_requires_authentication(client: TestClient) -> None:
    client.cookies.clear()
    for path in ("/api/v1/doses", "/api/v1/timeline", "/api/v1/medications", "/emergency"):
        assert client.get(path).status_code == 401, path


def test_polling_mode_does_not_expose_the_telegram_webhook(client: TestClient) -> None:
    """ADR-0008: the default outbound transport has no inbound integration route."""
    client.cookies.clear()
    assert client.post("/api/v1/integrations/telegram/webhook", json={}).status_code == 404


def test_login_does_not_reveal_whether_an_account_exists(client: TestClient) -> None:
    client.cookies.clear()
    unknown = client.post(
        "/api/v1/auth/login", json={"email": "nobody@example.com", "password": "x" * 12}
    )
    wrong = client.post("/api/v1/auth/login", json={"email": EMAIL, "password": "wrong-password"})
    assert unknown.status_code == wrong.status_code == 401
    assert unknown.json()["detail"] == wrong.json()["detail"]


def test_state_changing_requests_require_csrf(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    """A session cookie alone must never be enough to cause a write (T1)."""
    response = client.post("/api/v1/medications", json={"name": "x", "default_unit": "mg"})
    assert response.status_code == 403


def test_reads_do_not_require_csrf(client: TestClient, logged_in: dict[str, str]) -> None:
    assert client.get("/api/v1/medications").status_code == 200


# ---------------------------------------------------------------------------
# SAFE-02: category discriminator
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-02")
def test_plan_resources_declare_the_plan_category(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    created = client.post(
        "/api/v1/medications",
        json={"name": "Hydrocortisone", "default_unit": "mg", "default_route": "oral"},
        headers=logged_in,
    )
    assert created.status_code == 201, created.text
    assert created.json()["category"] == "plan"


@pytest.mark.safety("SAFE-02")
def test_fact_resources_declare_the_fact_category(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    medication_id = _a_medication(client, logged_in)
    dose = client.post(
        "/api/v1/doses",
        json={
            "medication_id": medication_id,
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": "2026-05-01T07:00:00", "timezone": "Europe/London"},
        },
        headers=logged_in,
    )
    assert dose.status_code == 201, dose.text
    assert dose.json()["category"] == "fact"


@pytest.mark.safety("SAFE-02")
def test_timeline_carries_a_category_per_item(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    _a_dose(client, logged_in)
    items = client.get("/api/v1/timeline").json()["items"]
    assert items
    assert all(item["category"] in {"fact", "plan", "ai"} for item in items)


# ---------------------------------------------------------------------------
# SAFE-16: approval is a human act with provenance
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-16")
def test_approval_requires_an_approver_and_a_source(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    version_id = _a_draft_regimen(client, logged_in)
    for payload in ({"approved_by": "Dr X"}, {"approval_source": "letter"}, {}):
        response = client.post(
            f"/api/v1/regimens/{version_id}/approve", json=payload, headers=logged_in
        )
        assert response.status_code == 422, payload


@pytest.mark.safety("SAFE-16")
def test_a_draft_is_not_the_active_plan(client: TestClient, logged_in: dict[str, str]) -> None:
    """An unapproved schedule must never be treated as the plan in force."""
    _a_draft_regimen(client, logged_in)
    assert client.get("/api/v1/regimens/active").json() is None


@pytest.mark.safety("SAFE-16")
def test_approved_version_cannot_be_approved_again(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    version_id = _a_draft_regimen(client, logged_in)
    approval = {"approved_by": "Dr Example", "approval_source": "clinic letter"}
    first = client.post(f"/api/v1/regimens/{version_id}/approve", json=approval, headers=logged_in)
    assert first.status_code == 200, first.text
    assert first.json()["approved_by"] == "Dr Example"

    second = client.post(f"/api/v1/regimens/{version_id}/approve", json=approval, headers=logged_in)
    assert second.status_code == 409


# ---------------------------------------------------------------------------
# SAFE-13: ambiguity is surfaced
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-13")
def test_ambiguous_dst_time_is_rejected_not_guessed(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    medication_id = _a_medication(client, logged_in)
    response = client.post(
        "/api/v1/doses",
        json={
            "medication_id": medication_id,
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            # 01:30 happens twice in London on this date.
            "time": {"local_time": "2026-10-25T01:30:00", "timezone": "Europe/London"},
        },
        headers=logged_in,
    )
    assert response.status_code == 422
    assert "occurs twice" in response.json()["detail"]


@pytest.mark.safety("SAFE-13")
def test_nonexistent_dst_time_is_rejected(client: TestClient, logged_in: dict[str, str]) -> None:
    medication_id = _a_medication(client, logged_in)
    response = client.post(
        "/api/v1/doses",
        json={
            "medication_id": medication_id,
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": "2026-03-29T01:30:00", "timezone": "Europe/London"},
        },
        headers=logged_in,
    )
    assert response.status_code == 422
    assert "does not exist" in response.json()["detail"]


# ---------------------------------------------------------------------------
# SAFE-07: exports keep the partition, AI off by default
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-07")
def test_export_separates_categories_and_excludes_ai_by_default(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    payload = client.post("/api/v1/exports", headers=logged_in).json()
    assert set(payload) >= {"plan", "facts", "ai"}
    assert payload["ai"] == {}, "AI content must be excluded unless asked for"
    assert "credentials" in payload["notice"]


# ---------------------------------------------------------------------------
# SAFE-10 / SAFE-27: comparison derives, and states its definition
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-10")
def test_missing_doses_are_derived_not_stored(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    before = len(client.get("/api/v1/doses").json())
    comparison = client.get(
        "/api/v1/doses/plan-comparison",
        params={"day": "2026-05-02", "timezone": "Europe/London"},
    )
    assert comparison.status_code == 200, comparison.text
    after = len(client.get("/api/v1/doses").json())
    assert before == after, "comparing a day must not create dose rows"


@pytest.mark.safety("SAFE-27")
def test_comparison_states_its_metric_definition_and_timezone(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    body = client.get(
        "/api/v1/doses/plan-comparison",
        params={"day": "2026-05-01", "timezone": "Europe/London"},
    ).json()
    assert body["timezone"] == "Europe/London"
    assert "30 minutes" in body["metric_definition"]
    assert "never stored as a zero dose" in body["metric_definition"]


# ---------------------------------------------------------------------------
# SAFE-21: the emergency page survives everything else being down
# ---------------------------------------------------------------------------


@pytest.mark.safety("SAFE-21")
def test_emergency_page_renders_without_ai_or_javascript(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    response = client.get("/emergency")
    assert response.status_code == 200
    body = response.text
    assert "<script" not in body, "the emergency page must not depend on JavaScript"
    assert "emergency services" in body.lower()
    assert response.headers["cache-control"] == "no-store"


@pytest.mark.safety("SAFE-22")
def test_emergency_page_says_so_when_no_instructions_exist(
    client: TestClient, logged_in: dict[str, str]
) -> None:
    """HealthCurve must never invent emergency instructions."""
    body = client.get("/emergency").text
    assert "No physician-authored emergency instructions" in body
    assert "will not invent" in body


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _a_medication(client: TestClient, headers: dict[str, str]) -> str:
    existing = client.get("/api/v1/medications").json()
    if existing:
        return existing[0]["id"]
    created = client.post(
        "/api/v1/medications",
        json={"name": "Hydrocortisone", "default_unit": "mg", "default_route": "oral"},
        headers=headers,
    )
    return created.json()["id"]


def _a_dose(client: TestClient, headers: dict[str, str]) -> dict[str, Any]:
    return client.post(
        "/api/v1/doses",
        json={
            "medication_id": _a_medication(client, headers),
            "amount": "10",
            "unit": "mg",
            "route": "oral",
            "category": "scheduled",
            "time": {"local_time": "2026-05-01T07:00:00", "timezone": "Europe/London"},
        },
        headers=headers,
    ).json()


def _a_draft_regimen(client: TestClient, headers: dict[str, str]) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%S%f")
    response = client.post(
        "/api/v1/regimens",
        json={
            "version_label": f"draft {stamp}",
            "effective_from": f"20{int(stamp[2:4]) % 50 + 30}-01-01T00:00:00",
            "slots": [],
            "instructions": [],
        },
        headers=headers,
    )
    assert response.status_code == 201, response.text
    return response.json()["id"]
