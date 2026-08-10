from __future__ import annotations

import json
import subprocess
import uuid
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import pytest
from sqlalchemy.orm import Session

from healthcurve.config import Settings
from healthcurve.identity.models import Owner
from healthcurve.integrations.telegram import handlers
from healthcurve.integrations.telegram.beads_bridge import (
    BridgeError,
    create_or_find_issue,
    load_envelope,
    process_one,
)
from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.dispatch import UpdateOutcome, process_update
from healthcurve.integrations.telegram.feature_requests import (
    FeatureRequestRejected,
    queue_request,
    validate_request,
)

NOW = datetime(2026, 8, 9, 12, tzinfo=UTC)


class FakeTelegramClient:
    def __init__(self, succeeds: bool = True) -> None:
        self.succeeds = succeeds
        self.messages: list[tuple[int, str]] = []

    def send_message(self, chat_id: int, text: str, **_kwargs: Any) -> bool:
        self.messages.append((chat_id, text))
        return self.succeeds


def settings(root: Path | None) -> Settings:
    return Settings(
        _env_file=None,  # type: ignore[call-arg]
        ollama_base_url="http://ollama:11434",
        beads_outbox_dir=root,
        beads_backlog_epic_id="hc-inbox",
    )


def test_handler_rejects_empty_oversized_and_private_values_and_fails_closed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "get_settings", lambda: settings(None))
    session = cast(Session, object())
    owner = cast(Owner, object())
    unavailable = handlers.handle_message(
        session,
        owner,
        text="/beads-add add hydration",
        message_id="1",
        now=NOW,  # type: ignore[arg-type]
    )
    assert "temporarily unavailable" in unavailable.text

    monkeypatch.setattr(handlers, "get_settings", lambda: settings(tmp_path))
    empty = handlers.handle_message(
        session,
        owner,
        text="/beads-add",
        message_id="2",
        now=NOW,  # type: ignore[arg-type]
    )
    oversized = handlers.handle_message(
        session,
        owner,
        text=f"/beads-add {'x' * 501}",
        message_id="3",
        now=NOW,  # type: ignore[arg-type]
    )
    private = handlers.handle_message(
        session,
        owner,
        text="/beads-add remember 15 mg at noon",
        message_id="4",
        now=NOW,  # type: ignore[arg-type]
    )
    assert empty.text.startswith("Usage:")
    assert "500 characters" in oversized.text
    assert "personal health values" in private.text
    assert list((tmp_path / "pending").glob("*.json")) == []


def test_dispatch_allowlist_and_update_claim_guard_outbox_creation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "get_settings", lambda: settings(tmp_path))
    owner = Owner(
        id=uuid.uuid4(),
        email="feature-owner@example.test",
        password_hash="synthetic-not-a-real-hash",
        default_timezone="UTC",
    )
    client = cast(TelegramClient, FakeTelegramClient())
    rejected_session = MagicMock(spec=Session)
    rejected_session.scalar.return_value = None
    rejected = {
        "update_id": 10,
        "message": {
            "message_id": 500,
            "chat": {"id": 9999, "type": "private"},
            "text": "/beads-add add hydration tracking",
        },
    }
    assert (
        process_update(
            cast(Session, rejected_session),
            rejected,
            allowed_chat_id=4242,
            client=client,
        )
        is UpdateOutcome.REJECTED_CHAT
    )
    assert not (tmp_path / "pending").exists()

    accepted_session = MagicMock(spec=Session)
    accepted_session.scalar.side_effect = [None, owner, uuid.uuid4()]
    accepted = {
        "update_id": 11,
        "message": {
            "message_id": 501,
            "chat": {"id": 4242, "type": "private"},
            "text": "/beads-add add hydration tracking",
        },
    }
    assert (
        process_update(
            cast(Session, accepted_session),
            accepted,
            allowed_chat_id=4242,
            client=client,
        )
        is UpdateOutcome.PROCESSED
    )
    assert len(list((tmp_path / "pending").glob("*.json"))) == 1
    assert (
        process_update(
            cast(Session, accepted_session),
            accepted,
            allowed_chat_id=4242,
            client=client,
        )
        is UpdateOutcome.DUPLICATE
    )
    assert len(list((tmp_path / "pending").glob("*.json"))) == 1


def test_hostile_shell_text_is_preserved_as_inert_argv_and_fixed_fields(tmp_path: Path) -> None:
    request = 'add hydration; $(touch /tmp/nope) "quoted"\nwith a second line'
    queued = queue_request(
        tmp_path, message_id="42", text=request, backlog_epic_id="hc-inbox", now=NOW
    )
    envelope = load_envelope(queued.path)
    calls: list[tuple[str, ...]] = []

    def runner(argv: Sequence[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        calls.append(tuple(argv))
        if "list" in argv:
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        return subprocess.CompletedProcess(argv, 0, "hc-safe.1\n", "")

    issue_id = create_or_find_issue(envelope, repo=tmp_path, bd_path="/fixed/bd", runner=runner)
    assert issue_id == "hc-safe.1"
    create_argv = calls[1]
    assert create_argv[0:2] == ("/fixed/bd", "create")
    assert "--priority" in create_argv and create_argv[create_argv.index("--priority") + 1] == "P2"
    assert (
        "--parent" in create_argv and create_argv[create_argv.index("--parent") + 1] == "hc-inbox"
    )
    assert "--description" in create_argv
    assert request in create_argv[create_argv.index("--description") + 1]
    assert all("shell" not in argument.lower() for argument in create_argv[:2])


def test_queue_and_bridge_are_idempotent_across_delivery_failure(tmp_path: Path) -> None:
    first = queue_request(
        tmp_path,
        message_id="77",
        text="add a feature that allows me to record hydration",
        backlog_epic_id="hc-inbox",
        now=NOW,
    )
    second = queue_request(
        tmp_path,
        message_id="77",
        text="add a feature that allows me to record hydration",
        backlog_epic_id="hc-inbox",
        now=NOW,
    )
    assert not first.already_queued and second.already_queued
    create_calls = 0

    def runner(argv: Sequence[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        nonlocal create_calls
        if len(argv) > 1 and argv[1] == "list":
            return subprocess.CompletedProcess(argv, 0, "[]", "")
        if len(argv) > 1 and argv[1] == "create":
            create_calls += 1
            return subprocess.CompletedProcess(argv, 0, "hc-inbox.1\n", "")
        return subprocess.CompletedProcess(argv, 0, "Push complete.\n", "")

    failing = FakeTelegramClient(succeeds=False)
    with pytest.raises(BridgeError, match="telegram_ack_failed"):
        process_one(
            first.path,
            root=tmp_path,
            repo=tmp_path,
            chat_id=4242,
            client=failing,  # type: ignore[arg-type]
            backlog_epic_id="hc-inbox",
            bd_path="/fixed/bd",
            runner=runner,
        )
    assert first.path.exists()
    successful = FakeTelegramClient()
    assert (
        process_one(
            first.path,
            root=tmp_path,
            repo=tmp_path,
            chat_id=4242,
            client=successful,  # type: ignore[arg-type]
            backlog_epic_id="hc-inbox",
            bd_path="/fixed/bd",
            runner=runner,
        )
        == "hc-inbox.1"
    )
    assert create_calls == 1
    assert successful.messages[0][0] == 4242
    assert "nothing was executed" in successful.messages[0][1]
    assert (tmp_path / "completed" / first.path.name).exists()


def test_host_bridge_rejects_worker_selected_parent(tmp_path: Path) -> None:
    queued = queue_request(
        tmp_path,
        message_id="78",
        text="add a feature that allows me to record hydration",
        backlog_epic_id="hc-untrusted",
        now=NOW,
    )
    with pytest.raises(BridgeError, match="outbox_parent_mismatch"):
        process_one(
            queued.path,
            root=tmp_path,
            repo=tmp_path,
            chat_id=4242,
            client=FakeTelegramClient(),  # type: ignore[arg-type]
            backlog_epic_id="hc-inbox",
            bd_path="/fixed/bd",
        )


def test_existing_external_reference_is_reused_and_bd_unavailable_is_observable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queued = queue_request(
        tmp_path,
        message_id="99",
        text="add a printable medication card",
        backlog_epic_id="hc-inbox",
        now=NOW,
    )
    envelope = load_envelope(queued.path)

    def runner(argv: Sequence[str], _cwd: Path) -> subprocess.CompletedProcess[str]:
        body: list[dict[str, Any]] = [
            {"id": "hc-inbox.9", "external_ref": f"telegram-feature:{envelope.request_id}"}
        ]
        return subprocess.CompletedProcess(argv, 0, json.dumps(body), "")

    assert (
        create_or_find_issue(envelope, repo=tmp_path, bd_path="/fixed/bd", runner=runner)
        == "hc-inbox.9"
    )

    def no_bd(_name: str) -> None:
        return None

    monkeypatch.setattr("healthcurve.integrations.telegram.beads_bridge.shutil.which", no_bd)
    with pytest.raises(BridgeError, match="beads_cli_unavailable"):
        create_or_find_issue(envelope, repo=tmp_path, bd_path=None, runner=runner)


@pytest.mark.parametrize(
    "text",
    ["token abc", "owner@example.test", "blood pressure 120 mmHg", "weight 180 lb"],
)
def test_private_or_secret_bearing_requests_are_rejected(text: str) -> None:
    with pytest.raises(FeatureRequestRejected, match="private_data"):
        validate_request(text)
