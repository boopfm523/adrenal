"""Trusted host consumer for locally normalized Telegram Beads proposals."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Final

from pydantic import ValidationError

from healthcurve.integrations.telegram.client import TelegramClient
from healthcurve.integrations.telegram.feature_requests import (
    ALLOWED_AREA_LABELS,
    ALLOWED_RISK_LABELS,
    FEATURE_REQUEST_PROMPT_VERSION,
    FEATURE_REQUEST_SCHEMA_VERSION,
    OUTBOX_SCHEMA_VERSION,
    FeatureRequestEvaluationFailed,
    FeatureRequestProposal,
    validate_proposal,
)
from healthcurve.logging import get_logger

log = get_logger(__name__)
_ISSUE_ID: Final = re.compile(r"^hc-[a-z0-9.]+$")
_REQUEST_ID: Final = re.compile(r"^tg-[a-f0-9]{24}$")
_MODEL_IDENTITY: Final = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:/-]{0,127}$")
_VERSION: Final = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
_WORD: Final = re.compile(r"[a-z0-9]+")
_STOPWORDS: Final = frozenset(
    {
        "a",
        "add",
        "allow",
        "allows",
        "an",
        "and",
        "feature",
        "for",
        "healthcurve",
        "i",
        "in",
        "me",
        "my",
        "of",
        "the",
        "to",
        "with",
    }
)
_WORKFLOW_ACCEPTANCE: Final = (
    "The normal AGENTS.md claim-review-test-close workflow remains required before "
    "implementation; this request does not authorize execution or status changes."
)


class BridgeError(RuntimeError):
    """Privacy-safe operational bridge failure."""


@dataclass(frozen=True, slots=True)
class Envelope:
    request_id: str
    proposal: FeatureRequestProposal
    backlog_epic_id: str
    model_name: str
    model_digest: str | None
    prompt_version: str
    proposal_schema_version: str


@dataclass(frozen=True, slots=True)
class IssueResolution:
    issue_id: str
    title: str
    created: bool


type Runner = Callable[[Sequence[str], Path], subprocess.CompletedProcess[str]]


def _run(argv: Sequence[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    try:
        return subprocess.run(  # noqa: S603 -- fixed executable and argv; never a shell
            list(argv), cwd=cwd, text=True, capture_output=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(list(argv), 127, "", "")


def load_envelope(path: Path) -> Envelope:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        request_id = raw["request_id"]
        parent = raw["backlog_epic_id"]
        provenance = raw["provenance"]
        proposal = FeatureRequestProposal.model_validate(raw["proposal"])
        validate_proposal(proposal)
    except (
        OSError,
        json.JSONDecodeError,
        KeyError,
        TypeError,
        ValidationError,
        FeatureRequestEvaluationFailed,
    ) as exc:
        raise BridgeError("outbox_envelope_invalid") from exc
    if not isinstance(raw, dict) or set(raw) != {
        "schema_version",
        "request_id",
        "backlog_epic_id",
        "source",
        "created_at",
        "proposal",
        "provenance",
    }:
        raise BridgeError("outbox_envelope_invalid")
    if not isinstance(provenance, dict) or set(provenance) != {
        "model_name",
        "model_digest",
        "prompt_version",
        "schema_version",
    }:
        raise BridgeError("outbox_envelope_invalid")
    model_name = provenance.get("model_name")
    model_digest = provenance.get("model_digest")
    prompt_version = provenance.get("prompt_version")
    proposal_schema_version = provenance.get("schema_version")
    if (
        raw.get("schema_version") != OUTBOX_SCHEMA_VERSION
        or raw.get("source") != "telegram_allowlisted_chat"
        or not _valid_created_at(raw.get("created_at"))
        or not isinstance(request_id, str)
        or _REQUEST_ID.fullmatch(request_id) is None
        or not isinstance(parent, str)
        or _ISSUE_ID.fullmatch(parent) is None
        or proposal.decision != "create"
        or not isinstance(model_name, str)
        or _MODEL_IDENTITY.fullmatch(model_name) is None
        or (model_digest is not None and not _valid_digest(model_digest))
        or not isinstance(prompt_version, str)
        or _VERSION.fullmatch(prompt_version) is None
        or not isinstance(proposal_schema_version, str)
        or _VERSION.fullmatch(proposal_schema_version) is None
        or prompt_version != FEATURE_REQUEST_PROMPT_VERSION
        or proposal_schema_version != FEATURE_REQUEST_SCHEMA_VERSION
        or any(label not in ALLOWED_AREA_LABELS for label in proposal.area_labels)
        or any(label not in ALLOWED_RISK_LABELS for label in proposal.risk_labels)
    ):
        raise BridgeError("outbox_envelope_invalid")
    return Envelope(
        request_id,
        proposal,
        parent,
        model_name,
        model_digest,
        prompt_version,
        proposal_schema_version,
    )


def _valid_digest(value: object) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[a-f0-9]{32,128}", value) is not None


def _valid_created_at(value: object) -> bool:
    if not isinstance(value, str) or len(value) > 40:
        return False
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        return False
    return parsed.tzinfo is not None and parsed.utcoffset() is not None


def _list_issues(repo: Path, *, bd: str, runner: Runner) -> list[dict[str, Any]]:
    completed = runner((bd, "list", "--all", "--limit", "0", "--json"), repo)
    if completed.returncode != 0:
        raise BridgeError("beads_list_failed")
    try:
        issues = json.loads(completed.stdout)
    except (json.JSONDecodeError, TypeError) as exc:
        raise BridgeError("beads_list_invalid") from exc
    if not isinstance(issues, list) or not all(isinstance(issue, dict) for issue in issues):
        raise BridgeError("beads_list_invalid")
    return issues


def _issue_id(issue: dict[str, Any]) -> str | None:
    identifier = issue.get("id")
    return (
        identifier
        if isinstance(identifier, str) and _ISSUE_ID.fullmatch(identifier) is not None
        else None
    )


def _strong_duplicate(
    proposal: FeatureRequestProposal, issues: list[dict[str, Any]]
) -> IssueResolution | None:
    title = proposal.title or ""
    normalized_title = " ".join(_WORD.findall(title.lower()))
    candidate_tokens = _tokens(" ".join([title, *proposal.search_terms]))
    for issue in issues:
        identifier = _issue_id(issue)
        existing_title = issue.get("title")
        if identifier is None or not isinstance(existing_title, str):
            continue
        existing_normalized = " ".join(_WORD.findall(existing_title.lower()))
        if normalized_title and existing_normalized == normalized_title:
            return IssueResolution(identifier, existing_title, False)
        haystack = " ".join(
            str(issue.get(field) or "").lower()
            for field in ("title", "description", "design", "acceptance_criteria")
        )
        matching_terms = {
            term.lower() for term in proposal.search_terms if term.lower() in haystack
        }
        existing_tokens = _tokens(haystack)
        union = candidate_tokens | existing_tokens
        similarity = len(candidate_tokens & existing_tokens) / len(union) if union else 0.0
        if len(matching_terms) >= 2 and similarity >= 0.45:
            return IssueResolution(identifier, existing_title, False)
    return None


def _tokens(value: str) -> set[str]:
    return {word for word in _WORD.findall(value.lower()) if word not in _STOPWORDS}


def create_or_find_issue(
    envelope: Envelope,
    *,
    repo: Path,
    bd_path: str | None = None,
    runner: Runner = _run,
) -> IssueResolution:
    """Create fixed safe fields, deduplicate, and recover idempotently after a crash."""
    bd = bd_path or shutil.which("bd")
    if bd is None:
        raise BridgeError("beads_cli_unavailable")
    issues = _list_issues(repo, bd=bd, runner=runner)
    external_ref = f"telegram-feature:{envelope.request_id}"
    for issue in issues:
        if issue.get("external_ref") == external_ref:
            identifier = _issue_id(issue)
            title = issue.get("title")
            if identifier is not None and isinstance(title, str):
                return IssueResolution(identifier, title, False)
    duplicate = _strong_duplicate(envelope.proposal, issues)
    if duplicate is not None:
        return duplicate

    proposal = envelope.proposal
    title = proposal.title or ""
    labels = sorted(
        {"source:telegram", "area:product", *proposal.area_labels, *proposal.risk_labels}
    )
    model_identity = envelope.model_name
    if envelope.model_digest:
        model_identity = f"{model_identity}@{envelope.model_digest}"
    notes = (
        f"Source: allowlisted Telegram request {envelope.request_id}. "
        f"Normalized locally by {model_identity}; prompt {envelope.prompt_version}; "
        f"schema {envelope.proposal_schema_version}. Raw Telegram content is not retained "
        "in this Bead."
    )
    acceptance = f"{proposal.acceptance_criteria}\n\n{_WORKFLOW_ACCEPTANCE}"
    completed = runner(
        (
            bd,
            "create",
            "--silent",
            "--title",
            title,
            "--type",
            "feature",
            "--priority",
            "P2",
            "--labels",
            ",".join(labels),
            "--parent",
            envelope.backlog_epic_id,
            "--external-ref",
            external_ref,
            "--description",
            proposal.description or "",
            "--design",
            proposal.design or "",
            "--acceptance",
            acceptance,
            "--notes",
            notes,
        ),
        repo,
    )
    identifier = completed.stdout.strip()
    if completed.returncode != 0 or _ISSUE_ID.fullmatch(identifier) is None:
        raise BridgeError("beads_create_failed")
    return IssueResolution(identifier, title, True)


def _write_result(root: Path, envelope: Envelope, resolution: IssueResolution) -> Path:
    results = root / "results"
    results.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = results / f"{envelope.request_id}.json"
    temporary = destination.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(
            {
                "request_id": envelope.request_id,
                "issue_id": resolution.issue_id,
                "title": resolution.title,
                "created": resolution.created,
            },
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, destination)
    return destination


def _load_result(path: Path, envelope: Envelope) -> IssueResolution:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        issue_id = raw["issue_id"]
        title = raw["title"]
        created = raw["created"]
    except (OSError, json.JSONDecodeError, KeyError, TypeError) as exc:
        raise BridgeError("outbox_result_invalid") from exc
    if (
        raw.get("request_id") != envelope.request_id
        or not isinstance(issue_id, str)
        or _ISSUE_ID.fullmatch(issue_id) is None
        or not isinstance(title, str)
        or not 1 <= len(title) <= 120
        or not isinstance(created, bool)
    ):
        raise BridgeError("outbox_result_invalid")
    return IssueResolution(issue_id, title, created)


def process_one(
    request_path: Path,
    *,
    root: Path,
    repo: Path,
    chat_id: int,
    client: TelegramClient,
    backlog_epic_id: str,
    bd_path: str | None = None,
    runner: Runner = _run,
) -> str:
    envelope = load_envelope(request_path)
    if envelope.backlog_epic_id != backlog_epic_id:
        raise BridgeError("outbox_parent_mismatch")
    result_path = root / "results" / f"{envelope.request_id}.json"
    if result_path.exists():
        resolution = _load_result(result_path, envelope)
    else:
        resolution = create_or_find_issue(envelope, repo=repo, bd_path=bd_path, runner=runner)
        _write_result(root, envelope, resolution)
    pushed = runner((bd_path or shutil.which("bd") or "bd", "dolt", "push"), repo)
    if pushed.returncode != 0:
        raise BridgeError("beads_sync_failed")
    action = "Added" if resolution.created else "Already tracked as"
    if not client.send_message(
        chat_id,
        f"{action} {resolution.issue_id}: {resolution.title}\n"
        "It remains in the feature inbox for a later normal agent run; nothing was executed.",
    ):
        raise BridgeError("telegram_ack_failed")
    completed = root / "completed"
    completed.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.replace(request_path, completed / request_path.name)
    return resolution.issue_id


def run_once(
    *,
    root: Path,
    repo: Path,
    chat_id: int,
    client: TelegramClient,
    backlog_epic_id: str,
) -> tuple[int, int]:
    processed = failed = 0
    for request_path in sorted((root / "pending").glob("tg-*.json")):
        try:
            process_one(
                request_path,
                root=root,
                repo=repo,
                chat_id=chat_id,
                client=client,
                backlog_epic_id=backlog_epic_id,
            )
        except BridgeError as exc:
            failed += 1
            log.warning("feature bridge request failed", reason_code=str(exc), outcome="retrying")
        else:
            processed += 1
            log.info("feature bridge request completed", outcome="completed")
    return processed, failed


def run_loop(
    *,
    root: Path,
    repo: Path,
    chat_id: int,
    client: TelegramClient,
    backlog_epic_id: str,
    interval_s: float = 10,
) -> None:
    while True:
        run_once(
            root=root,
            repo=repo,
            chat_id=chat_id,
            client=client,
            backlog_epic_id=backlog_epic_id,
        )
        time.sleep(interval_s)
