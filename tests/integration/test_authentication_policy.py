"""Password-only application authentication behind the Tailscale boundary."""

from __future__ import annotations

from healthcurve.app import create_app
from healthcurve.config import Settings


def test_healthcurve_mfa_routes_are_not_registered() -> None:
    app = create_app(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            ollama_base_url="http://ollama:11434",
        )
    )
    paths = set(app.openapi()["paths"])

    assert not any(
        path == "/api/v1/auth/mfa" or path.startswith("/api/v1/auth/mfa/") for path in paths
    )


def test_login_schema_accepts_only_email_and_password() -> None:
    app = create_app(
        Settings(
            _env_file=None,  # pyright: ignore[reportCallIssue]
            ollama_base_url="http://ollama:11434",
        )
    )
    properties = app.openapi()["components"]["schemas"]["LoginRequest"]["properties"]

    assert set(properties) == {"email", "password"}
