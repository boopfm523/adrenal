"""Top-level isolated restore entry point that wires operations to the API."""

from __future__ import annotations

import json
import secrets
import sys
import uuid
from collections.abc import Generator, Sequence
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import Engine, select
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.orm import Session, sessionmaker

from healthcurve import models as _all_models
from healthcurve.api import deps
from healthcurve.app import create_app
from healthcurve.config import Settings
from healthcurve.identity import service as auth
from healthcurve.identity.models import Owner
from healthcurve.operations.backup import BackupError
from healthcurve.operations.restore_drill import DrillSettings, run_drill

del _all_models  # importing it registers cross-schema ORM relationships


def _temporary_owner_login(factory: sessionmaker[Session]) -> tuple[str, str]:
    password = secrets.token_urlsafe(32)
    try:
        with factory() as session, session.begin():
            owners = list(session.scalars(select(Owner)))
            if len(owners) != 1:
                raise BackupError("restore_owner_count_invalid")
            owner = owners[0]
            owner.password_hash = auth.hash_password(password)
            owner.failed_login_count = 0
            owner.locked_until = None
            return owner.email, password
    except BackupError:
        raise
    except SQLAlchemyError as exc:
        raise BackupError("restore_owner_login_setup_failed") from exc


def assert_api_smoke(
    engine: Engine,
    ai_database_url: str,
    uploads: Path,
    reports: Path,
) -> None:
    factory = sessionmaker(engine, expire_on_commit=False)
    email, password = _temporary_owner_login(factory)

    def override() -> Generator[Session]:
        session = factory()
        try:
            yield session
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()

    settings = Settings(
        _env_file=None,  # type: ignore[call-arg]
        database_url=str(engine.url),
        ai_database_url=ai_database_url,
        redis_url=None,
        ollama_base_url="http://ollama:11434",
        uploads_dir=uploads,
        report_artifacts_dir=reports,
    )
    app = create_app(settings)
    app.dependency_overrides[deps.session_scope] = override
    try:
        with TestClient(app) as client:
            if client.get("/health/live").json() != {"status": "ok"}:
                raise BackupError("restore_api_liveness_failed")
            if client.get("/health/ready").json() != {"status": "ok"}:
                raise BackupError("restore_api_readiness_failed")
            login = client.post("/api/v1/auth/login", json={"email": email, "password": password})
            if login.status_code != 200:
                raise BackupError("restore_api_login_failed")
            try:
                csrf = login.json()["csrf_token"]
            except (KeyError, TypeError, json.JSONDecodeError) as exc:
                raise BackupError("restore_api_login_failed") from exc
            timeline = client.get("/api/v1/timeline")
            if timeline.status_code != 200 or not isinstance(timeline.json().get("items"), list):
                raise BackupError("restore_api_timeline_failed")
            exported = client.post(
                "/api/v1/privacy/export",
                headers={
                    auth.CSRF_HEADER_NAME: csrf,
                    "Idempotency-Key": f"restore-smoke-{uuid.uuid4()}",
                },
                json={"password": password},
            )
            export_status = exported.json()
            if (
                exported.status_code != 202
                or not isinstance(export_status, dict)
                or export_status.get("status") != "queued"
                or not export_status.get("job_id")
            ):
                raise BackupError("restore_api_export_failed")
    except BackupError:
        raise
    except Exception as exc:
        raise BackupError("restore_api_smoke_failed") from exc
    finally:
        app.dependency_overrides.clear()


def main(_argv: Sequence[str] | None = None) -> int:
    try:
        result = run_drill(DrillSettings.from_env(), api_smoke=assert_api_smoke)
    except BackupError as exc:
        print(json.dumps({"status": "failed", "reason_code": exc.reason_code}, sort_keys=True))
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
