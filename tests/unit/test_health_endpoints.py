"""Health endpoints must expose nothing (threat model T2, plan section 7)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from healthcurve.app import create_app
from healthcurve.config import Environment, Settings


def _client(environment: Environment = Environment.DEV) -> TestClient:
    settings = Settings(environment=environment, ollama_base_url="http://ollama:11434")
    return TestClient(create_app(settings))


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_returns_status_only(path: str) -> None:
    response = _client().get(path)
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_leaks_no_version_or_counts(path: str) -> None:
    body = _client().get(path).text.lower()
    for leak in ("version", "0.1.0", "postgres", "ollama", "count", "database"):
        assert leak not in body


@pytest.mark.parametrize("path", ["/health/live", "/health/ready"])
def test_health_is_not_cached(path: str) -> None:
    """T7: health-bearing responses must not linger on a shared device."""
    assert _client().get(path).headers["cache-control"] == "no-store"


def test_production_disables_docs_and_schema() -> None:
    """T2: no interactive docs or schema on the public production surface."""
    client = _client(Environment.PROD)
    assert client.get("/docs").status_code == 404
    assert client.get("/redoc").status_code == 404
    assert client.get("/api/v1/openapi.json").status_code == 404


def test_development_keeps_docs_available() -> None:
    client = _client(Environment.DEV)
    assert client.get("/docs").status_code == 200
    assert client.get("/api/v1/openapi.json").status_code == 200
