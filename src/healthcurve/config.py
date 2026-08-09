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
from pathlib import Path
from typing import Any, Final, Self
from urllib.parse import urlparse

from pydantic import Field, SecretStr, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class TelegramMode(StrEnum):
    """How Telegram updates reach us (ADR-0008).

    ``POLLING`` needs no public endpoint and is the default. ``WEBHOOK`` requires a
    publicly reachable HTTPS host and is only usable on a public deployment.
    """

    POLLING = "polling"
    WEBHOOK = "webhook"


class Environment(StrEnum):
    DEV = "dev"
    STAGING = "staging"
    PROD = "prod"


class PublicOllamaError(ValueError):
    """Raised when the configured Ollama URL is not on a private network."""


#: Docker Desktop reserves these for the host gateway. They cannot resolve to a public
#: address, and they do not resolve at all outside a container -- so the resolution
#: check below would reject a legitimately private target. Matched exactly, never by
#: suffix: "host.docker.internal.example.com" is somebody else's domain.
DOCKER_HOST_ALIASES: Final = frozenset({"host.docker.internal", "gateway.docker.internal"})


def is_private_host(host: str) -> bool:
    """True if every address ``host`` resolves to is private, loopback, or link-local.

    A hostname is accepted only if *all* of its addresses are private -- a name that
    resolves to both a private and a public address is treated as public.
    """
    if host in DOCKER_HOST_ALIASES:
        return True

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
    #: The restricted role used only where model output becomes a draft (SAFE-15/16).
    #: Unset means the AI path shares the privileged connection, which downgrades those
    #: rules from a database privilege to a convention -- refused in production.
    ai_database_url: str | None = None

    # --- Local LLM (ADR-0003). Never public. ---
    ollama_base_url: str = "http://ollama:11434"
    ollama_model: str = "qwen3:30b"
    ollama_vision_model: str = "qwen3-vl:30b"
    ollama_connect_timeout_s: float = Field(default=5.0, gt=0)
    ollama_read_timeout_s: float = Field(default=60.0, gt=0)
    ollama_max_retries: int = Field(default=2, ge=0)
    #: Reasoning models "think" before answering. Extraction is a parsing task with a
    #: fixed output schema, and measurement showed the reasoning phase cost 15x the
    #: latency with no accuracy gain, so it is off by default. Set true to compare.
    ollama_thinking: bool = False

    # --- Telegram (docs/telegram-setup.md). All three are class C8 secrets. ---
    #: Mounted JSON key ring for encrypted credentials (threat model C8). The file,
    #: not its contents, is configured here. Production rejects plaintext provider
    #: secrets in environment variables.
    credential_key_file: Path | None = None
    telegram_bot_token: SecretStr | None = None
    #: Verified on every webhook request, constant-time. Without it, anyone who learns
    #: the URL can post updates (threat model T4).
    telegram_webhook_secret: SecretStr | None = None
    #: Only this chat is processed. Everything else is dropped and counted.
    telegram_allowed_chat_id: int | None = None
    #: Default polling: it works behind NAT and on a private tailnet (ADR-0008).
    telegram_mode: TelegramMode = TelegramMode.POLLING
    #: Public HTTPS base for the webhook, e.g. https://health.example.com
    public_base_url: str | None = None

    # --- Owner scoping (single-owner product; see docs/threat-model.md) ---
    owner_email: str | None = None

    # --- Time ---
    default_timezone: str = "UTC"

    # --- Durable worker queue (ADR-0004) ---
    job_poll_interval_s: float = Field(default=2.0, gt=0, le=60)

    # --- Sensitive local artifacts (ADR-0010) ---
    #: Exact source documents live outside the web root. In Compose this path is a
    #: bind mount shared only with the network-isolated document worker and backup.
    uploads_dir: Path = Path("var/uploads")

    @model_validator(mode="before")
    @classmethod
    def _empty_string_means_unset(cls, data: Any) -> Any:
        """Treat an empty environment variable as absent.

        Docker Compose's ``${VAR:-}`` always sets the variable, so an unconfigured
        integration arrives as ``""`` rather than being missing. Without this, an
        optional int like the Telegram chat id fails to parse and the process
        crash-loops at startup -- which is a confusing way to say "not configured".
        """
        if isinstance(data, dict):
            return {k: v for k, v in data.items() if v != ""}
        return data

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

    @model_validator(mode="after")
    def _validate_ai_role_is_separate_in_prod(self) -> Self:
        """SAFE-15/16 are database privileges, not conventions.

        Sharing one connection would let a prompt-injected extraction write straight
        into the record. In development the fallback is allowed so a bare checkout
        runs, but it is logged loudly by :func:`healthcurve.db.get_ai_engine`.
        """
        if self.environment is Environment.PROD and not self.ai_database_url:
            raise ValueError(
                "HC_AI_DATABASE_URL must be set in production (SAFE-15, SAFE-16): "
                "the extraction path must connect as a role that is denied writes to "
                "the fact and plan schemas.",
            )
        if self.ai_database_url and self.ai_database_url == self.database_url:
            raise ValueError(
                "HC_AI_DATABASE_URL must not equal HC_DATABASE_URL: pointing both at "
                "the same role defeats SAFE-15/16 while appearing to satisfy them.",
            )
        return self

    @model_validator(mode="after")
    def _validate_production_credentials_are_encrypted(self) -> Self:
        if self.environment is Environment.PROD and (
            self.telegram_bot_token is not None or self.telegram_webhook_secret is not None
        ):
            raise ValueError(
                "plaintext Telegram secrets are forbidden in production; store them with "
                "healthcurve credential-set and mount HC_CREDENTIAL_KEY_FILE (class C8)"
            )
        if (
            self.environment is Environment.PROD
            and self.telegram_allowed_chat_id is not None
            and self.credential_key_file is None
        ):
            raise ValueError(
                "HC_CREDENTIAL_KEY_FILE is required when Telegram is enabled in production"
            )
        return self

    @property
    def telegram_configured(self) -> bool:
        """True only when everything the *selected transport* needs is present.

        The chat allow-list is required in both modes: a bot that answers anyone is a
        bot anyone can put data into. The webhook secret is required only in webhook
        mode, because polling has no inbound endpoint for it to protect (ADR-0008).
        """
        if self.telegram_bot_token is None or self.telegram_allowed_chat_id is None:
            return False
        if self.telegram_mode is TelegramMode.WEBHOOK:
            return self.telegram_webhook_secret is not None
        return True


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()
