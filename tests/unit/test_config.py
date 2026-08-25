"""Configuration safety checks: ADR-0003 private Ollama, threat model T2 debug rule."""

from __future__ import annotations

import pytest

from healthcurve.config import (
    Environment,
    PublicOllamaError,
    Settings,
    TelegramMode,
    is_private_host,
)


def _settings(**overrides: object) -> Settings:
    """Settings built in isolation from the developer's .env.

    Without `_env_file=None` these tests read whatever happens to be configured
    locally, which silently invalidates them -- a real .env once made a
    "not configured" assertion pass as configured.
    """
    base: dict[str, object] = {"_env_file": None, "ollama_base_url": "http://ollama:11434"}
    base.update(overrides)
    return Settings(**base)  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "url",
    [
        "http://ollama:11434",  # compose service name
        "http://127.0.0.1:11434",
        "http://localhost:11434",
        "http://10.0.0.5:11434",
        "http://192.168.1.20:11434",
        "http://172.16.3.4:11434",
    ],
)
def test_private_ollama_urls_are_accepted(url: str) -> None:
    assert _settings(ollama_base_url=url).ollama_base_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://8.8.8.8:11434",
        "https://ollama.example.com:11434",
        "http://1.1.1.1",
    ],
)
def test_public_ollama_urls_are_rejected_at_startup(url: str) -> None:
    """ADR-0003: a public Ollama is a boot failure, never a warning."""
    with pytest.raises(ValueError, match="private network"):
        _settings(ollama_base_url=url)


def test_ollama_url_without_host_is_rejected() -> None:
    with pytest.raises(ValueError, match="no host"):
        _settings(ollama_base_url="not-a-url")


def test_public_ollama_error_is_a_value_error() -> None:
    """Pydantic wraps validator errors; the type must stay catchable as ValueError."""
    assert issubclass(PublicOllamaError, ValueError)


def test_production_rejects_plaintext_integration_secrets() -> None:
    with pytest.raises(ValueError, match="plaintext Telegram secrets are forbidden"):
        _settings(
            environment="prod",
            ai_database_url="postgresql+psycopg://healthcurve_ai@postgres/healthcurve",
            telegram_bot_token="synthetic-token",
        )


def test_production_telegram_metadata_requires_external_key_file() -> None:
    with pytest.raises(ValueError, match="HC_CREDENTIAL_KEY_FILE is required"):
        _settings(
            environment="prod",
            ai_database_url="postgresql+psycopg://healthcurve_ai@postgres/healthcurve",
            telegram_allowed_chat_id=123,
        )


def test_debug_is_rejected_in_production() -> None:
    """Threat model T2: interactive tracebacks leak health data and internals."""
    with pytest.raises(ValueError, match="debug must be disabled in production"):
        _settings(environment=Environment.PROD, debug=True)


def test_debug_is_allowed_outside_production() -> None:
    assert _settings(environment=Environment.DEV, debug=True).debug is True


def test_defaults_are_safe() -> None:
    settings = _settings()
    assert settings.debug is False
    assert settings.environment is Environment.DEV
    assert settings.ollama_model == "qwen3:30b"
    assert settings.garmin_sync_hour_local == 12
    assert settings.garmin_sync_interval_hours == 12


def test_garmin_sync_interval_must_divide_the_owner_local_day() -> None:
    with pytest.raises(ValueError, match="must divide evenly into 24"):
        _settings(garmin_sync_interval_hours=5)


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("10.1.2.3", True),
        ("169.254.1.1", True),
        ("8.8.8.8", False),
        ("ollama", True),  # single-label name cannot be public
        # Docker Desktop's host gateway: reserved, and unresolvable outside a container.
        ("host.docker.internal", True),
        ("gateway.docker.internal", True),
        # Matched exactly. A suffix match here would accept an attacker's domain.
        ("host.docker.internal.evil.com", False),
        ("nothost.docker.internal", False),
    ],
)
def test_is_private_host(host: str, expected: bool) -> None:
    assert is_private_host(host) is expected


# ---------------------------------------------------------------------------
# Empty environment variables
# ---------------------------------------------------------------------------


def test_empty_env_vars_are_treated_as_unset() -> None:
    """Regression: docker compose's ${VAR:-} sets an empty string, not nothing.

    The worker crash-looped at startup because an empty HC_TELEGRAM_ALLOWED_CHAT_ID
    could not be parsed as an int -- a confusing way to express "not configured".
    """
    settings = _settings(
        telegram_bot_token="",
        telegram_allowed_chat_id="",
        telegram_webhook_secret="",
        public_base_url="",
    )
    assert settings.telegram_bot_token is None
    assert settings.telegram_allowed_chat_id is None
    assert settings.telegram_webhook_secret is None
    assert settings.telegram_configured is False


def test_empty_string_does_not_override_a_default() -> None:
    """An empty variable must fall back to the default, not blank the setting."""
    settings = _settings(default_timezone="")
    assert settings.default_timezone == "UTC"


def test_telegram_configuration_is_transport_aware() -> None:
    """Polling needs no webhook secret; webhook mode does (ADR-0008)."""
    common: dict[str, object] = {"telegram_bot_token": "1:A", "telegram_allowed_chat_id": 42}

    assert _settings(**common).telegram_configured is True
    assert _settings(**common, telegram_mode=TelegramMode.WEBHOOK).telegram_configured is False
    assert (
        _settings(
            **common, telegram_mode=TelegramMode.WEBHOOK, telegram_webhook_secret="s"
        ).telegram_configured
        is True
    )


# ---------------------------------------------------------------------------
# The restricted AI role (SAFE-15, SAFE-16)
# ---------------------------------------------------------------------------


def test_production_requires_a_separate_ai_role() -> None:
    """Without it, SAFE-15/16 stop being database privileges and become a convention."""
    with pytest.raises(ValueError, match="HC_AI_DATABASE_URL must be set in production"):
        _settings(environment=Environment.PROD)


def test_pointing_both_urls_at_the_same_role_is_rejected() -> None:
    """The dangerous case: it looks configured while enforcing nothing."""
    url = "postgresql+psycopg://healthcurve@postgres:5432/healthcurve"
    with pytest.raises(ValueError, match="must not equal"):
        _settings(database_url=url, ai_database_url=url)


def test_development_may_omit_the_ai_role() -> None:
    """A bare checkout has to run; db.get_ai_engine logs the downgrade loudly."""
    assert _settings(environment=Environment.DEV).ai_database_url is None


def test_the_chat_allow_list_is_required_in_both_modes() -> None:
    """A bot that answers anyone is a bot anyone can put data into."""
    for mode in (TelegramMode.POLLING, TelegramMode.WEBHOOK):
        settings = _settings(
            telegram_bot_token="1:A", telegram_webhook_secret="s", telegram_mode=mode
        )
        assert settings.telegram_configured is False
