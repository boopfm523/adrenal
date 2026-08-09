"""Resolve Telegram class-C8 secrets without exposing them to callers as strings."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.config import Environment, Settings, TelegramMode
from healthcurve.identity.models import Owner
from healthcurve.integrations.credentials import CredentialKeyRing, get_credential


@dataclass(frozen=True)
class TelegramSecrets:
    bot_token: SecretStr | None
    webhook_secret: SecretStr | None

    def configured_for(self, settings: Settings) -> bool:
        if self.bot_token is None or settings.telegram_allowed_chat_id is None:
            return False
        return settings.telegram_mode is not TelegramMode.WEBHOOK or self.webhook_secret is not None


def load_telegram_secrets(session: Session, settings: Settings) -> TelegramSecrets:
    """Prefer the encrypted store; allow environment secrets only outside production."""
    bot_token: SecretStr | None = None
    webhook_secret: SecretStr | None = None
    if settings.credential_key_file is not None:
        owner = session.scalar(select(Owner).limit(1))
        if owner is not None:
            ring = CredentialKeyRing.from_file(settings.credential_key_file)
            bot_token = get_credential(
                session,
                owner_id=owner.id,
                provider="telegram",
                name="bot_token",
                key_ring=ring,
            )
            webhook_secret = get_credential(
                session,
                owner_id=owner.id,
                provider="telegram",
                name="webhook_secret",
                key_ring=ring,
            )
    if settings.environment is not Environment.PROD:
        bot_token = bot_token or settings.telegram_bot_token
        webhook_secret = webhook_secret or settings.telegram_webhook_secret
    return TelegramSecrets(bot_token=bot_token, webhook_secret=webhook_secret)
