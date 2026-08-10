"""Sanitized file outbox for Telegram product requests.

The application container deliberately has neither the repository nor ``bd``. It
writes bounded request envelopes here; a separately trusted host bridge consumes
them using fixed argv. Telegram content is always data, never instructions.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Final

MAX_REQUEST_LENGTH: Final = 500
MIN_REQUEST_LENGTH: Final = 8
_SECRET_OR_PERSONAL: Final = re.compile(
    r"(?i)(\b(?:password|passcode|token)\b|api[ _-]?key|bot[ _-]?token|"
    r"bearer\s+[a-z0-9._-]+|"
    r"\b\d+(?:\.\d+)?\s*(?:mg|mcg|mmhg|bpm|kg|lb|mmol/l|mg/dl)\b|"
    r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b)"
)


class FeatureRequestRejected(ValueError):
    """A privacy-safe request validation failure."""


@dataclass(frozen=True, slots=True)
class QueuedFeatureRequest:
    request_id: str
    path: Path
    already_queued: bool


def validate_request(text: str) -> str:
    request = text.strip()
    if len(request) < MIN_REQUEST_LENGTH:
        raise FeatureRequestRejected("request_too_short")
    if len(request) > MAX_REQUEST_LENGTH:
        raise FeatureRequestRejected("request_too_long")
    if "\x00" in request or _SECRET_OR_PERSONAL.search(request):
        raise FeatureRequestRejected("request_may_contain_private_data")
    return request


def queue_request(
    root: Path,
    *,
    message_id: str,
    text: str,
    backlog_epic_id: str,
    now: datetime | None = None,
) -> QueuedFeatureRequest:
    """Atomically create one request envelope per Telegram message id."""
    if not message_id or len(message_id) > 80 or not message_id.isdecimal():
        raise FeatureRequestRejected("message_id_invalid")
    request = validate_request(text)
    request_id = "tg-" + hashlib.sha256(message_id.encode("ascii")).hexdigest()[:24]
    pending = root / "pending"
    pending.mkdir(parents=True, exist_ok=True, mode=0o700)
    destination = pending / f"{request_id}.json"
    if destination.exists() or (root / "completed" / f"{request_id}.json").exists():
        return QueuedFeatureRequest(request_id, destination, True)
    payload = {
        "schema_version": 1,
        "request_id": request_id,
        "request": request,
        "backlog_epic_id": backlog_epic_id,
        "source": "telegram_allowlisted_chat",
        "created_at": (now or datetime.now(UTC)).isoformat(),
    }
    descriptor, temporary_name = tempfile.mkstemp(prefix=".request-", suffix=".tmp", dir=pending)
    try:
        with os.fdopen(descriptor, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, sort_keys=True)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(temporary_name, 0o600)
        os.replace(temporary_name, destination)
    finally:
        Path(temporary_name).unlink(missing_ok=True)
    return QueuedFeatureRequest(request_id, destination, False)
