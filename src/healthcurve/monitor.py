"""On-host operational monitor and off-host Telegram alert sender.

The message contains reason codes only. It never includes health records, owner
identity, request paths, prompts, filenames, or exception text.
"""

from __future__ import annotations

import argparse
import json
import signal
import sys
import threading
from datetime import UTC, datetime, timedelta
from types import FrameType

from healthcurve.config import Environment, Settings, get_settings
from healthcurve.db import get_session_factory
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.secrets import TelegramSecrets, load_telegram_secrets
from healthcurve.logging import configure_logging, get_logger
from healthcurve.monitoring import MonitoringSnapshot, collect_snapshot

log = get_logger(__name__)
_stop = threading.Event()
REMINDER_INTERVAL = timedelta(hours=6)


def _handle_signal(signum: int, _frame: FrameType | None) -> None:
    log.info("monitor stopping", reason_code=signal.Signals(signum).name)
    _stop.set()


def _message(snapshot: MonitoringSnapshot, *, recovered: bool = False) -> str:
    if recovered:
        return "HealthCurve recovered: all configured operational checks are healthy."
    reasons = "\n".join(f"- {reason}" for reason in snapshot.reason_codes)
    return f"HealthCurve operational alert\n{reasons}\n\nSee docs/operations-runbook.md."


def _load_alert_delivery(settings: Settings) -> tuple[TelegramClient, int] | None:
    with get_session_factory()() as session:
        secrets: TelegramSecrets = load_telegram_secrets(session, settings)
    if secrets.bot_token is None or settings.telegram_allowed_chat_id is None:
        return None
    return TelegramClient(settings, token=secrets.bot_token), settings.telegram_allowed_chat_id


def _snapshot(settings: Settings) -> MonitoringSnapshot:
    with get_session_factory()() as session:
        return collect_snapshot(session, settings)


def run(settings: Settings, *, once: bool = False) -> int:
    delivery = _load_alert_delivery(settings)
    if delivery is None:
        log.error(
            "monitor alert delivery unavailable",
            outcome="failed",
            reason_code="telegram_not_configured",
        )
        return 1
    telegram, chat_id = delivery
    previous_state: str | None = None
    last_alert_at: datetime | None = None

    while not _stop.is_set():
        try:
            snapshot = _snapshot(settings)
        except Exception as exc:
            # Exception type is operational; exception text can contain DB parameters.
            log.error(
                "monitor collection failed",
                outcome="failed",
                reason_code=type(exc).__name__,
            )
            sent = telegram.send_message(
                chat_id,
                "HealthCurve operational alert\n- monitoring_collection_failed\n\n"
                "See docs/operations-runbook.md.",
            )
            if once:
                return 2 if sent else 1
            _stop.wait(settings.monitor_interval_s)
            continue

        now = datetime.now(UTC)
        reminder_due = last_alert_at is None or now - last_alert_at >= REMINDER_INTERVAL
        if snapshot.state == "alert" and (previous_state != "alert" or reminder_due):
            if telegram.send_message(chat_id, _message(snapshot)):
                last_alert_at = now
        elif snapshot.state == "healthy" and previous_state == "alert":
            telegram.send_message(chat_id, _message(snapshot, recovered=True))
        previous_state = snapshot.state

        if once:
            print(json.dumps(snapshot.as_dict(), sort_keys=True))
            return 2 if snapshot.state == "alert" else 0
        _stop.wait(settings.monitor_interval_s)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--once", action="store_true", help="check, alert, print JSON, and exit")
    args = parser.parse_args(argv)
    settings = get_settings()
    configure_logging(json_output=settings.environment is not Environment.DEV)
    signal.signal(signal.SIGTERM, _handle_signal)
    signal.signal(signal.SIGINT, _handle_signal)
    return run(settings, once=args.once)


if __name__ == "__main__":
    sys.exit(main())
