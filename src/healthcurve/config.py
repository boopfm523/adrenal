"""Application configuration.

Settings come from the environment. Two rules here are safety controls rather than
conveniences, and both fail at startup rather than degrading quietly:

* ``ollama_base_url`` must resolve to a private address (ADR-0003). A publicly
  reachable Ollama is the single most dangerous misconfiguration in this system, so
  it is a boot failure, not a warning.
* ``debug`` cannot be enabled in the production environment (threat model T2).
"""

from __future__ import annotations

import ipaddress
import socket
from enum import StrEnum
from functools import lru_cache
from typing import Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class PublicOllamaError(ValueError):
    """Raised when the configured Ollama URL is not on a private network."""


def is_private_host(host: str) -> bool:
    """True if every address ``host`` resolves to is private, loopback, or link-local.

    A hostname is accepted only if *all* of its addresses are private -- a name that
    resolves to both a private and a public address is treated as public.
    """
    try:
        addr = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        return addr.is_private or addr.is_loopback or addr.is_link_local

    try:
        infos = socket.getaddrinfo(host, None)
    except socket.gaierror:
        # A name we cannot resolve at boot is most likely a Compose service name on
        # the internal network that is not up yet. Docker-style single-label names
        # cannot be public, so accept them; anything dotted must resolve to prove it.
        return "." not in host

    resolved = {info[4][0] for info in infos}
    if not resolved:
        return "." not in host
    return all(
        (lambda a: a.is_private or a.is_loopback or a.is_link_local)(ipaddress.ip_address(str(r)))
        for r in resolved
    )


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="HC_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    environment: Environment = Environment.DEV
    debug: bool = False

    # --- Database (ADR-0001) ---
    database_url: str = "postgresql+psycopg://healthcurve@localhost:5432/healthcurve"
    database_password: SecretStr | None = None

    # --- Local LLM (ADR-0003). Never public. ---
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3-coder"
    ollama_connect_timeout_s: float = Field(default=5.0, gt=0)
    ollama_read_timeout_s: float = Field(default=60.0, gt=0)
    ollama_max_retries: int = Field(default=2, ge=0)

    # --- Telegram (docs/telegram-setup.md). All three are class C8 secrets. ---
    telegram_bot_token: SecretStr | None = None
    #: Verified on every webhook request, constant-time. Without it, anyone who learns
    #: the URL can post updates (threat model T4).
    telegram_webhook_secret: SecretStr | None = None
    #: Only this chat is processed. Everything else is dropped and counted.
    telegram_allowed_chat_id: int | None = None
    #: Public HTTPS base for the webhook, e.g. https://health.example.com
    public_base_url: str | None = None

    # --- Owner scoping (single-owner product; see docs/threat-model.md) ---
    owner_email: str | None = None

    # --- Time ---
    default_timezone: str = "UTC"

    @model_validator(mode="after")
    def _validate_ollama_is_private(self) -> Self:
        parsed = urlparse(self.ollama_base_url)
        host = parsed.hostname
        if not host:
            raise PublicOllamaError(
                f"HC_OLLAMA_BASE_URL has no host: {self.ollama_base_url!r}",
            )
        if not is_private_host(host):
            raise PublicOllamaError(
                f"HC_OLLAMA_BASE_URL must be on a private network (ADR-0003); "
                f"{host!r} resolves to a public address. Ollama is unauthenticated "
                f"and must never be publicly reachable.",
            )
        return self

    @model_validator(mode="after")
    def _validate_debug_not_in_prod(self) -> Self:
        if self.debug and self.environment is Environment.PROD:
            raise ValueError(
                "debug must be disabled in production (docs/threat-model.md T2): "
                "interactive tracebacks leak health data and internals.",
            )
        return self

    @property
    def telegram_configured(self) -> bool:
        """True only when every part needed to run the bot safely is present.

        Deliberately all-or-nothing: a token without a webhook secret, or without an
        allow-listed chat, is a bot anyone can write to (threat model T4).
        """
        return (
            self.telegram_bot_token is not None
            and self.telegram_webhook_secret is not None
            and self.telegram_allowed_chat_id is not None
        )


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
