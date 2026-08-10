"""Run the narrow host-side Telegram-to-Beads bridge."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from healthcurve.config import get_settings
from healthcurve.integrations.telegram.beads_bridge import run_loop, run_once
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.logging import configure_logging


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    arguments = parser.parse_args()
    settings = get_settings()
    configure_logging(json_output=False)
    if settings.beads_outbox_dir is None or settings.telegram_allowed_chat_id is None:
        print("bridge configuration is incomplete", file=sys.stderr)
        return 2
    client = TelegramClient(settings)
    if not client.configured:
        print("Telegram credentials are unavailable", file=sys.stderr)
        return 2
    if arguments.once:
        _, failed = run_once(
            root=settings.beads_outbox_dir,
            repo=arguments.repo.resolve(),
            chat_id=settings.telegram_allowed_chat_id,
            client=client,
            backlog_epic_id=settings.beads_backlog_epic_id,
        )
        return 1 if failed else 0
    run_loop(
        root=settings.beads_outbox_dir,
        repo=arguments.repo.resolve(),
        chat_id=settings.telegram_allowed_chat_id,
        client=client,
        backlog_epic_id=settings.beads_backlog_epic_id,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
