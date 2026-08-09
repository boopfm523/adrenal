from __future__ import annotations

import os
import time
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest import mock

import pyotp
import pytest
from alembic import command
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

from healthcurve.api import deps
from healthcurve.app import create_app
from healthcurve.config import Settings, get_settings
from healthcurve.identity import service as auth
from healthcurve.identity.models import MfaRecoveryCode, Owner
from healthcurve.integrations.credentials import IntegrationCredential, create_key_file
from healthcurve.operations.audit import AuditAction, AuditEntry

pytestmark = [pytest.mark.postgres, pytest.mark.slow]

ROOT = Path(__file__).resolve().parents[2]
EMAIL = "mfa-owner@example.com"
PASSWORD = "synthetic-mfa-password"


@pytest.fixture(scope="module")
def postgres() -> Iterator[PostgresContainer]:
    with PostgresContainer(
        "postgres:16-alpine",
        username="healthcurve",
        password="test-password",
        dbname="healthcurve",
        driver="psycopg",
    ) as running:
        yield running


@pytest.fixture(scope="module")
def mfa_client(
    postgres: PostgresContainer, tmp_path_factory: pytest.TempPathFactory
) -> Iterator[tuple[TestClient, sessionmaker[Session]]]:
    temporary = tmp_path_factory.mktemp("mfa")
    key_file = temporary / "credential-keys.json"
    create_key_file(key_file, "test_key")
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=postgres.get_connection_url(),
        ollama_base_url="http://ollama:11434",
        credential_key_file=key_file,
    )
    engine = create_engine(settings.database_url)
    with engine.begin() as connection:
        for schema in ("fact", "plan", "ai", "ops", "identity"):
            connection.execute(text(f"CREATE SCHEMA IF NOT EXISTS {schema}"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS btree_gist"))
        connection.execute(text("CREATE EXTENSION IF NOT EXISTS pgcrypto"))
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "migrations"))
    with mock.patch.dict(os.environ, {"HC_DATABASE_URL": settings.database_url}):
        get_settings.cache_clear()
        command.upgrade(config, "head")
    get_settings.cache_clear()

    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as session, session.begin():
        session.add(
            Owner(
                email=EMAIL,
                password_hash=auth.hash_password(PASSWORD),
                default_timezone="UTC",
            )
        )

    def override() -> Iterator[Session]:
        with factory() as session:
            try:
                yield session
                session.commit()
            except Exception:
                session.rollback()
                raise

    app = create_app(settings)
    app.dependency_overrides[deps.session_scope] = override
    with TestClient(app) as client:
        yield client, factory
    engine.dispose()


def _login(client: TestClient, code: str | None = None):
    payload = {"email": EMAIL, "password": PASSWORD}
    if code is not None:
        payload["second_factor_code"] = code
    return client.post("/api/v1/auth/login", json=payload)


def test_enrollment_login_recovery_and_removal_are_enforced_and_audited(
    mfa_client: tuple[TestClient, sessionmaker[Session]],
) -> None:
    client, factory = mfa_client
    initial = _login(client)
    assert initial.status_code == 200
    csrf = {auth.CSRF_HEADER_NAME: initial.json()["csrf_token"]}

    started = client.post("/api/v1/auth/mfa/enrollment", headers=csrf, json={"password": PASSWORD})
    assert started.status_code == 200, started.text
    secret = started.json()["secret"]
    assert secret in started.json()["provisioning_uri"]
    confirmed = client.post(
        "/api/v1/auth/mfa/enrollment/confirm",
        headers=csrf,
        json={"code": pyotp.TOTP(secret).now()},
    )
    assert confirmed.status_code == 200, confirmed.text
    recovery_codes = confirmed.json()["recovery_codes"]
    assert len(recovery_codes) == len(set(recovery_codes)) == 10

    with factory() as session:
        owner = session.scalar(select(Owner).where(Owner.email == EMAIL))
        assert owner is not None and owner.mfa_enabled
        credential = session.scalar(
            select(IntegrationCredential).where(IntegrationCredential.provider == "mfa")
        )
        assert credential is not None
        assert secret.encode() not in credential.ciphertext
        stored_codes = list(session.scalars(select(MfaRecoveryCode)))
        assert len(stored_codes) == 10
        assert all(code not in {row.code_hash for row in stored_codes} for code in recovery_codes)
        assert session.scalar(
            select(AuditEntry).where(AuditEntry.action == AuditAction.MFA_ENROLLED)
        )

    client.post("/api/v1/auth/logout", headers=csrf)
    missing = _login(client)
    assert missing.status_code == 401
    assert missing.json()["detail"] == "second factor required"
    assert _login(client, "000000").status_code == 401

    # The verifier accepts one adjacent TOTP window for modest clock skew.
    future_code = pyotp.TOTP(secret).at(int(time.time()) + 30)
    authenticated = _login(client, future_code)
    assert authenticated.status_code == 200, authenticated.text
    second_csrf = {auth.CSRF_HEADER_NAME: authenticated.json()["csrf_token"]}
    client.post("/api/v1/auth/logout", headers=second_csrf)
    assert _login(client, future_code).status_code == 401  # replay blocked

    recovered = _login(client, recovery_codes[0])
    assert recovered.status_code == 200
    recovery_csrf = {auth.CSRF_HEADER_NAME: recovered.json()["csrf_token"]}
    regenerated = client.post(
        "/api/v1/auth/mfa/recovery-codes",
        headers=recovery_csrf,
        json={"password": PASSWORD, "code": recovery_codes[1]},
    )
    assert regenerated.status_code == 200
    replacements = regenerated.json()["recovery_codes"]
    assert len(replacements) == 10
    client.post("/api/v1/auth/logout", headers=recovery_csrf)
    assert _login(client, recovery_codes[0]).status_code == 401
    assert _login(client, recovery_codes[2]).status_code == 401

    final_login = _login(client, replacements[0])
    final_csrf = {auth.CSRF_HEADER_NAME: final_login.json()["csrf_token"]}
    removed = client.request(
        "DELETE",
        "/api/v1/auth/mfa",
        headers=final_csrf,
        json={"password": PASSWORD, "code": replacements[1]},
    )
    assert removed.status_code == 204
    assert client.get("/api/v1/auth/me").status_code == 401
    app = cast(Any, client.app)
    app.state.settings.mfa_required = True
    blocked = _login(client)
    assert blocked.status_code == 403
    assert "MFA enrollment is required" in blocked.json()["detail"]
    with factory() as session:
        owner = session.scalar(select(Owner).where(Owner.email == EMAIL))
        assert owner is not None and not owner.mfa_enabled
        assert session.scalar(select(MfaRecoveryCode)) is None
        actions = set(session.scalars(select(AuditEntry.action)))
        assert {
            AuditAction.MFA_ENROLLED,
            AuditAction.MFA_RECOVERY_CODE_USED,
            AuditAction.MFA_RECOVERY_CODES_REGENERATED,
            AuditAction.MFA_REMOVED,
        } <= actions
