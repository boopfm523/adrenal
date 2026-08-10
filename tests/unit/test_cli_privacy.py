from __future__ import annotations

from argparse import Namespace
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from healthcurve.cli import telegram_status


def test_telegram_status_reports_state_without_identifiers_or_provider_error_text(
    capsys: pytest.CaptureFixture[str],
) -> None:
    settings = SimpleNamespace(
        telegram_allowed_chat_id=123456789,
        public_base_url="https://private.example.test",
    )
    secrets = SimpleNamespace(bot_token="fixture", webhook_secret="fixture")
    factory = MagicMock()
    client = MagicMock()
    client.get_me.return_value = {
        "ok": True,
        "result": {"username": "private_fixture_bot", "first_name": "Private Fixture"},
    }
    client.get_webhook_info.return_value = {
        "ok": True,
        "result": {
            "url": "https://private.example.test/webhook",
            "pending_update_count": 2,
            "last_error_message": "provider detail that must not be displayed",
        },
    }

    with (
        patch("healthcurve.cli.get_settings", return_value=settings),
        patch("healthcurve.cli.get_session_factory", return_value=factory),
        patch(
            "healthcurve.integrations.telegram.secrets.load_telegram_secrets",
            return_value=secrets,
        ),
        patch("healthcurve.integrations.telegram.client.TelegramClient", return_value=client),
    ):
        assert telegram_status(Namespace()) == 0

    output = capsys.readouterr().out
    assert "Allowed chat set:   yes" in output
    assert "Public URL set:     yes" in output
    assert "Telegram API connection: verified" in output
    assert "Webhook configured: yes" in output
    assert "Last webhook error: present (details redacted)" in output
    for private_value in (
        "123456789",
        "private.example.test",
        "private_fixture_bot",
        "Private Fixture",
        "provider detail that must not be displayed",
    ):
        assert private_value not in output
