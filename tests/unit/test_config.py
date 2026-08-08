"""Configuration safety checks: ADR-0003 private Ollama, threat model T2 debug rule."""

from __future__ import annotations

import pytest

from healthcurve.config import Environment, PublicOllamaError, Settings, is_private_host


def _settings(**overrides: object) -> Settings:
    base: dict[str, object] = {"ollama_base_url": "http://ollama:11434"}
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


@pytest.mark.parametrize(
    ("host", "expected"),
    [
        ("127.0.0.1", True),
        ("::1", True),
        ("10.1.2.3", True),
        ("169.254.1.1", True),
        ("8.8.8.8", False),
        ("ollama", True),  # single-label name cannot be public
    ],
)
def test_is_private_host(host: str, expected: bool) -> None:
    assert is_private_host(host) is expected
