"""Class-C8 credentials remain useless without the external key file."""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import Engine, create_engine, select, text
from sqlalchemy.orm import Session, sessionmaker
from testcontainers.community.postgres import PostgresContainer

import healthcurve.models  # noqa: F401  # pyright: ignore[reportUnusedImport]
from healthcurve.config import Settings
from healthcurve.db import SCHEMAS, Base
from healthcurve.identity.models import Owner
from healthcurve.integrations.credentials import (
    CredentialDecryptionError,
    CredentialKeyRing,
    IntegrationCredential,
    add_active_key,
    create_key_file,
    delete_credential,
    get_credential,
    rotate_credentials,
    set_credential,
)
from healthcurve.integrations.telegram.secrets import load_telegram_secrets

pytestmark = [pytest.mark.postgres, pytest.mark.slow]
PLAINTEXT = "synthetic-provider-token-never-store-this"


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


@pytest.fixture(scope="module")
def owner_id(engine: Engine) -> uuid.UUID:
    identifier = uuid.uuid4()
    owner = Owner(
        id=identifier,
        email="credential-test@example.com",
        password_hash="synthetic-not-a-real-hash",
        default_timezone="UTC",
    )
    with Session(engine) as session, session.begin():
        session.add(owner)
    return identifier


@pytest.fixture
def session(engine: Engine) -> Iterator[Session]:
    factory = sessionmaker(engine, expire_on_commit=False)
    with factory() as active:
        yield active
        active.rollback()
    with engine.begin() as connection:
        connection.execute(
            text("DELETE FROM ops.audit_entry WHERE target_type = 'integration_credential'")
        )
        connection.execute(text("DELETE FROM identity.integration_credential"))


@pytest.fixture
def key_file(tmp_path: Path) -> Path:
    path = tmp_path / "credential-keys.json"
    create_key_file(path, "key_one")
    return path


def test_database_row_contains_only_authenticated_ciphertext(
    session: Session, owner_id: uuid.UUID, key_file: Path
) -> None:
    ring = CredentialKeyRing.from_file(key_file)
    with session.begin():
        row = set_credential(
            session,
            owner_id=owner_id,
            provider="garmin",
            name="refresh_token",
            value=SecretStr(PLAINTEXT),
            key_ring=ring,
        )
        identifier = row.id

    stored = session.execute(
        text(
            "SELECT provider, name, key_id, encode(nonce, 'hex'), "
            "encode(ciphertext, 'hex') FROM identity.integration_credential WHERE id=:id"
        ),
        {"id": identifier},
    ).one()
    serialized_row = "|".join(stored)
    assert PLAINTEXT not in serialized_row
    assert "garmin" in serialized_row

    recovered = get_credential(
        session,
        owner_id=owner_id,
        provider="garmin",
        name="refresh_token",
        key_ring=ring,
    )
    assert recovered is not None
    assert recovered.get_secret_value() == PLAINTEXT
    assert PLAINTEXT not in repr(recovered)


def test_rotation_reencrypts_without_data_loss(
    session: Session, owner_id: uuid.UUID, key_file: Path
) -> None:
    old_ring = CredentialKeyRing.from_file(key_file)
    with session.begin():
        row = set_credential(
            session,
            owner_id=owner_id,
            provider="telegram",
            name="bot_token",
            value=SecretStr(PLAINTEXT),
            key_ring=old_ring,
        )
        old_ciphertext = row.ciphertext

    add_active_key(key_file, "key_two")
    new_ring = CredentialKeyRing.from_file(key_file)
    with session.begin():
        assert rotate_credentials(session, key_ring=new_ring, owner_id=owner_id) == 1

    stored = session.scalar(
        select(IntegrationCredential).where(
            IntegrationCredential.owner_id == owner_id,
            IntegrationCredential.provider == "telegram",
        )
    )
    assert stored is not None
    assert stored.key_id == "key_two"
    assert stored.ciphertext != old_ciphertext
    recovered = get_credential(
        session,
        owner_id=owner_id,
        provider="telegram",
        name="bot_token",
        key_ring=new_ring,
    )
    assert recovered is not None and recovered.get_secret_value() == PLAINTEXT


def test_ciphertext_cannot_be_copied_to_another_label(
    session: Session, owner_id: uuid.UUID, key_file: Path
) -> None:
    ring = CredentialKeyRing.from_file(key_file)
    with session.begin():
        first = set_credential(
            session,
            owner_id=owner_id,
            provider="weather",
            name="api_key",
            value=SecretStr(PLAINTEXT),
            key_ring=ring,
        )
        second = set_credential(
            session,
            owner_id=owner_id,
            provider="weather",
            name="api_secret",
            value=SecretStr("different-synthetic-secret"),
            key_ring=ring,
        )
        second.nonce = first.nonce
        second.ciphertext = first.ciphertext

    with pytest.raises(CredentialDecryptionError, match="credential_authentication_failed"):
        get_credential(
            session,
            owner_id=owner_id,
            provider="weather",
            name="api_secret",
            key_ring=ring,
        )


def test_disconnect_destroys_ciphertext(
    session: Session, owner_id: uuid.UUID, key_file: Path
) -> None:
    ring = CredentialKeyRing.from_file(key_file)
    with session.begin():
        row = set_credential(
            session,
            owner_id=owner_id,
            provider="synthetic",
            name="access_token",
            value=SecretStr(PLAINTEXT),
            key_ring=ring,
        )
        identifier = row.id
    with session.begin():
        assert delete_credential(
            session, owner_id=owner_id, provider="synthetic", name="access_token"
        )
    assert session.get(IntegrationCredential, identifier) is None


def test_telegram_runtime_prefers_encrypted_store_over_development_fallback(
    session: Session, owner_id: uuid.UUID, key_file: Path
) -> None:
    with session.begin():
        set_credential(
            session,
            owner_id=owner_id,
            provider="telegram",
            name="bot_token",
            value=SecretStr(PLAINTEXT),
            key_ring=CredentialKeyRing.from_file(key_file),
        )
    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        ollama_base_url="http://ollama:11434",
        credential_key_file=key_file,
        telegram_bot_token="development-fallback-token",
        telegram_allowed_chat_id=123,
    )
    loaded = load_telegram_secrets(session, settings)
    assert loaded.bot_token is not None
    assert loaded.bot_token.get_secret_value() == PLAINTEXT
    assert loaded.configured_for(settings)
