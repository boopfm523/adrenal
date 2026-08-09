"""End-to-end safety behaviour of the API.

Runs against real PostgreSQL with the real migrations, because most of what is asserted
here is only true if the database constraints exist.
"""

from __future__ import annotations

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest import mock

import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session
from testcontainers.community.postgres import PostgresContainer

from healthcurve.ai.models import ExtractionDraft
from healthcurve.config import Settings, get_settings
from healthcurve.document_worker import process_available, validate_one
from healthcurve.identity import service as auth
from healthcurve.identity.models import Owner
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminImportBatch,
    GarminMetricEvent,
    GarminSleepEvent,
)
from healthcurve.labs.documents import DocumentLayout
from healthcurve.labs.models import LabDocument, LabDocumentStatus, LabPanel, LabResult
from tests.fixtures.garmin import synthetic_activity_csv, synthetic_fit
from tests.fixtures.pdf import (
    OcrToolRunner,
    QpdfRunner,
    synthetic_scanned_lab_pdf,
    synthetic_text_lab_pdf,
)

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
def settings(postgres: PostgresContainer, tmp_path_factory: pytest.TempPathFactory) -> Settings:
    return Settings(
        # Never read the developer's .env: a real HC_OLLAMA_BASE_URL once made this
        # fixture fail startup validation for reasons unrelated to the test.
        _env_file=None,  # type: ignore[call-arg]
        database_url=postgres.get_connection_url(),
        ollama_base_url="http://ollama:11434",
        uploads_dir=tmp_path_factory.mktemp("api-uploads"),
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
    for path in (
        "/api/v1/doses",
        "/api/v1/timeline",
        "/api/v1/medications",
        "/api/v1/labs/documents/00000000-0000-0000-0000-000000000000",
    ):
        assert client.get(path).status_code == 401, path


def test_anonymous_emergency_page_is_useful_without_disclosing_owner_data(
    client: TestClient,
) -> None:
    client.cookies.clear()
    response = client.get("/emergency")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert "call your local emergency services" in body.lower()
    assert "medical id" in body.lower()
    assert "Private emergency details are locked" in body
    assert "physician-authored instructions</h2>" not in body
    assert "Log an emergency injection" not in body
    assert "<form" not in body
    assert (
        client.post(
            "/emergency/injection",
            data={"medication_id": str(uuid.uuid4()), "amount": "100"},
        ).status_code
        == 401
    )


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


def test_garmin_preview_then_confirm_is_idempotent_and_preserves_provenance(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    fit = synthetic_fit()
    upload = {"file": ("synthetic.fit", fit, "application/vnd.ant.fit")}

    with Session(engine) as session:
        before = session.scalar(select(func.count()).select_from(GarminImportBatch))

    preview = client.post(
        "/api/v1/integrations/garmin/imports/preview",
        files=upload,
        data={"timezone": "Europe/London"},
        headers=logged_in,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["creates_facts"] is False
    assert {"activity", "heart_rate", "sleep", "sleep_score"} <= set(body["observed_metrics"])
    assert "stress" in body["missing_metrics"]

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GarminImportBatch)) == before
        assert session.scalar(select(func.count()).select_from(GarminMetricEvent)) == 0

    mismatch = client.post(
        "/api/v1/integrations/garmin/imports/confirm",
        files=upload,
        data={"timezone": "Europe/London", "expected_sha256": "0" * 64},
        headers=logged_in,
    )
    assert mismatch.status_code == 409
    assert mismatch.json()["detail"]["code"] == "preview_checksum_mismatch"
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GarminImportBatch)) == before

    confirm_data = {
        "timezone": "Europe/London",
        "expected_sha256": body["source_sha256"],
    }
    first = client.post(
        "/api/v1/integrations/garmin/imports/confirm",
        files=upload,
        data=confirm_data,
        headers=logged_in,
    )
    assert first.status_code == 200, first.text
    assert first.json() == {
        "batch_id": first.json()["batch_id"],
        "source_sha256": body["source_sha256"],
        "created": True,
        "metric_count": 4,
        "sleep_count": 1,
        "activity_count": 1,
    }

    second = client.post(
        "/api/v1/integrations/garmin/imports/confirm",
        files=upload,
        data=confirm_data,
        headers=logged_in,
    )
    assert second.status_code == 200, second.text
    assert second.json()["created"] is False
    assert second.json()["batch_id"] == first.json()["batch_id"]

    with Session(engine) as session:
        batch = session.scalar(
            select(GarminImportBatch).where(
                GarminImportBatch.source_sha256 == body["source_sha256"]
            )
        )
        assert batch is not None
        assert batch.source_payload == fit
        assert batch.source_sha256 == body["source_sha256"]
        assert session.scalar(select(func.count()).select_from(GarminImportBatch)) == 1
        assert session.scalar(select(func.count()).select_from(GarminMetricEvent)) == 4
        assert session.scalar(select(func.count()).select_from(GarminSleepEvent)) == 1
        assert session.scalar(select(func.count()).select_from(GarminActivityEvent)) == 1
        metric = session.scalar(select(GarminMetricEvent))
        assert metric is not None
        assert metric.garmin_manufacturer == "garmin"
        assert metric.garmin_device_serial_hash
        assert metric.source_revision

    csv = synthetic_activity_csv()
    csv_upload = {"file": ("activities.csv", csv, "text/csv")}
    csv_preview = client.post(
        "/api/v1/integrations/garmin/imports/preview",
        files=csv_upload,
        data={"timezone": "Europe/London"},
        headers=logged_in,
    )
    assert csv_preview.status_code == 200, csv_preview.text
    assert csv_preview.json()["observed_metrics"] == ["activity"]
    csv_confirm = client.post(
        "/api/v1/integrations/garmin/imports/confirm",
        files=csv_upload,
        data={
            "timezone": "Europe/London",
            "expected_sha256": csv_preview.json()["source_sha256"],
        },
        headers=logged_in,
    )
    assert csv_confirm.status_code == 200, csv_confirm.text
    assert csv_confirm.json()["created"] is True
    assert csv_confirm.json()["activity_count"] == 1

    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(GarminImportBatch)) == 2
        assert session.scalar(select(func.count()).select_from(GarminActivityEvent)) == 2


def test_lab_csv_preview_flags_unknowns_then_confirm_is_idempotent(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    csv_payload = (
        b"Analyte,Value,Qualitative,Unit,Range,Flag\n"
        b"Known synthetic analyte,12.3,,nmol/L,5-10,H\n"
        b"Mystery synthetic analyte,,Not detected,,,\n"
        b"SYNTHETIC TEST,7,,units,1-9,\n"
    )
    upload = {"file": ("synthetic-labs.csv", csv_payload, "text/csv")}
    data = {
        "mapping_json": (
            '{"analyte":"Analyte","value":"Value","qualitative":"Qualitative",'
            '"unit":"Unit","reference_range":"Range","abnormal_flag":"Flag"}'
        ),
        "analyte_map_json": (
            '{"Known synthetic analyte":"known-code",'
            '"Synthetic Test":"first-code","synthetic test":"second-code"}'
        ),
        "specimen_local": "2026-08-09T07:30:00",
        "report_local": "2026-08-09T09:00:00",
        "timezone": "Europe/London",
    }
    with Session(engine) as session:
        before_panels = session.scalar(select(func.count()).select_from(LabPanel))
        before_results = session.scalar(select(func.count()).select_from(LabResult))
        assert before_panels is not None
        assert before_results is not None

    preview = client.post(
        "/api/v1/labs/imports/csv/preview",
        files=upload,
        data=data,
        headers=logged_in,
    )
    assert preview.status_code == 200, preview.text
    body = preview.json()
    assert body["creates_facts"] is False
    assert body["candidates"][0]["normalized_analyte_code"] == "known-code"
    assert body["candidates"][1]["flags"] == ["unrecognized_analyte"]
    assert body["candidates"][2]["flags"] == ["ambiguous_analyte"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabPanel)) == before_panels
        assert session.scalar(select(func.count()).select_from(LabResult)) == before_results

    mismatch = client.post(
        "/api/v1/labs/imports/csv/confirm",
        files=upload,
        data={**data, "expected_sha256": "0" * 64},
        headers=logged_in,
    )
    assert mismatch.status_code == 409
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabPanel)) == before_panels

    confirm_data = {**data, "expected_sha256": body["source_sha256"]}
    first = client.post(
        "/api/v1/labs/imports/csv/confirm",
        files=upload,
        data=confirm_data,
        headers=logged_in,
    )
    second = client.post(
        "/api/v1/labs/imports/csv/confirm",
        files=upload,
        data=confirm_data,
        headers=logged_in,
    )
    assert first.status_code == second.status_code == 200
    assert first.json()["created"] is True
    assert second.json()["created"] is False
    assert second.json()["panel_id"] == first.json()["panel_id"]
    assert first.json()["result_count"] == 3
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabPanel)) == before_panels + 1
        assert session.scalar(select(func.count()).select_from(LabResult)) == before_results + 3
        imported = session.scalar(
            select(LabPanel).where(LabPanel.id == uuid.UUID(first.json()["panel_id"]))
        )
        assert imported is not None
        assert imported.confirmation_state.value == "confirmed_from_draft"
        assert imported.results[1].qualitative_result == "Not detected"
        assert imported.results[1].normalized_analyte_code is None


def test_manual_qualitative_lab_entry_is_a_direct_fact(
    client: TestClient, logged_in: dict[str, str], engine: Engine
) -> None:
    response = client.post(
        "/api/v1/labs/manual",
        headers=logged_in,
        json={
            "specimen_time": {
                "local_time": "2026-08-08T10:15:00",
                "timezone": "Europe/London",
            },
            "report_time": {
                "local_time": "2026-08-08T12:30:00",
                "timezone": "Europe/London",
            },
            "laboratory_name": "Synthetic laboratory",
            "results": [
                {
                    "analyte_name": "Synthetic qualitative result",
                    "qualitative_result": "Not detected (verbatim)",
                    "abnormal_flag": "Lab supplied flag",
                }
            ],
        },
    )
    assert response.status_code == 201, response.text
    assert response.json()["category"] == "fact"
    with Session(engine) as session:
        panel = session.get(LabPanel, uuid.UUID(response.json()["panel_id"]))
        assert panel is not None
        assert panel.source_type.value == "web"
        assert panel.confirmation_state.value == "direct"
        assert panel.results[0].qualitative_result == "Not detected (verbatim)"
        assert panel.results[0].abnormal_flag == "Lab supplied flag"


def test_pdf_upload_is_quarantined_validated_downloaded_as_attachment_and_deleted(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    source = b"%PDF-1.7\nsynthetic fixture only\n"
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("../Synthetic report.pdf", source, "application/pdf")},
    )
    assert uploaded.status_code == 202, uploaded.text
    body = uploaded.json()
    assert body["status"] == "pending"
    assert body["display_name"] == "Synthetic report.pdf"
    document_id = uuid.UUID(body["document_id"])
    layout = DocumentLayout(settings.uploads_dir)
    assert layout.path("quarantine", document_id).read_bytes() == source

    validation = validate_one(layout, document_id, runner=QpdfRunner(pages=2))
    assert validation.status == "stored"
    status_response = client.get(f"/api/v1/labs/documents/{document_id}")
    assert status_response.status_code == 200
    assert status_response.json()["status"] == "stored"
    assert status_response.json()["page_count"] == 2

    downloaded = client.get(f"/api/v1/labs/documents/{document_id}/download")
    assert downloaded.status_code == 200
    assert downloaded.content == source
    assert downloaded.headers["content-disposition"].startswith("attachment;")
    assert downloaded.headers["x-content-type-options"] == "nosniff"
    assert downloaded.headers["cache-control"] == "no-store"

    deleted = client.delete(f"/api/v1/labs/documents/{document_id}", headers=logged_in)
    assert deleted.status_code == 204
    assert not layout.path("stored", document_id).exists()
    assert layout.path("tombstones", document_id, ".deleted").exists()
    with Session(engine) as session:
        document = session.get(LabDocument, document_id)
        assert document is not None
        assert document.status is LabDocumentStatus.DELETED
        assert document.display_name == "deleted.pdf"
        assert document.sha256 == "0" * 64


def test_pdf_upload_rejects_spoofed_content_type_and_signature(
    client: TestClient, logged_in: dict[str, str], settings: Settings
) -> None:
    wrong_type = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic.pdf", b"%PDF-1.7\n", "text/plain")},
    )
    wrong_signature = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic.pdf", b"not a PDF", "application/pdf")},
    )
    assert wrong_type.status_code == 415
    assert wrong_type.json()["detail"]["code"] == "pdf_media_type_invalid"
    assert wrong_signature.status_code == 422
    assert wrong_signature.json()["detail"]["code"] == "pdf_signature_invalid"

    structurally_bad = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic.pdf", b"%PDF-1.7\nmalformed", "application/pdf")},
    )
    assert structurally_bad.status_code == 202
    document_id = uuid.UUID(structurally_bad.json()["document_id"])
    result = validate_one(
        DocumentLayout(settings.uploads_dir), document_id, runner=QpdfRunner(check_code=2)
    )
    assert result.reason_code == "pdf_structure_invalid"
    rejected = client.get(f"/api/v1/labs/documents/{document_id}")
    assert rejected.json()["status"] == "rejected"
    assert rejected.json()["rejection_reason"] == "pdf_structure_invalid"


def test_digital_pdf_extraction_creates_only_review_draft_and_keeps_unparsed_rows(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    payload = synthetic_text_lab_pdf()
    with Session(engine) as session:
        facts_before = session.scalar(select(func.count()).select_from(LabResult))
        drafts_before = session.scalar(select(func.count()).select_from(ExtractionDraft))
        assert facts_before is not None
        assert drafts_before is not None
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={"file": ("synthetic-digital.pdf", payload, "application/pdf")},
    )
    assert uploaded.status_code == 202
    document_id = uuid.UUID(uploaded.json()["document_id"])
    process_available(DocumentLayout(settings.uploads_dir), runner=QpdfRunner())

    extraction = client.get(f"/api/v1/labs/documents/{document_id}/extraction")
    assert extraction.status_code == 200, extraction.text
    body = extraction.json()
    assert body["category"] == "ai_draft"
    assert body["model_name"] is None
    parsed = [candidate for candidate in body["candidates"] if candidate["parsed"]]
    unparsed = [candidate for candidate in body["candidates"] if not candidate["parsed"]]
    assert parsed[0]["extraction_tier"] == "embedded_text"
    assert parsed[0]["extractor_name"] == "pdfplumber"
    assert parsed[0]["requires_confirmation"] is True
    assert parsed[0]["analyte_name"] == "Synthetic sodium"
    assert {candidate["source_text"] for candidate in unparsed} == {
        "Synthetic laboratory panel",
        "Synthetic unparsed note",
    }
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabResult)) == facts_before
        assert (
            session.scalar(select(func.count()).select_from(ExtractionDraft)) == drafts_before + 1
        )


def test_scanned_pdf_ocr_path_remains_a_confirmation_required_draft(
    client: TestClient,
    logged_in: dict[str, str],
    engine: Engine,
    settings: Settings,
) -> None:
    with Session(engine) as session:
        facts_before = session.scalar(select(func.count()).select_from(LabResult))
    uploaded = client.post(
        "/api/v1/labs/documents",
        headers=logged_in,
        files={
            "file": (
                "synthetic-scan.pdf",
                synthetic_scanned_lab_pdf(),
                "application/pdf",
            )
        },
    )
    assert uploaded.status_code == 202
    document_id = uuid.UUID(uploaded.json()["document_id"])
    process_available(DocumentLayout(settings.uploads_dir), runner=OcrToolRunner())

    extraction = client.get(f"/api/v1/labs/documents/{document_id}/extraction")
    assert extraction.status_code == 200, extraction.text
    candidates = extraction.json()["candidates"]
    parsed = [candidate for candidate in candidates if candidate["parsed"]]
    unparsed = [candidate for candidate in candidates if not candidate["parsed"]]
    assert parsed[0]["extraction_tier"] == "ocr"
    assert parsed[0]["coordinate_space"] == "rendered_pixels"
    assert parsed[0]["requires_confirmation"] is True
    assert "low_confidence" in unparsed[0]["flags"]
    with Session(engine) as session:
        assert session.scalar(select(func.count()).select_from(LabResult)) == facts_before


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
