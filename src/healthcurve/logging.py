"""Structured logging with allow-list redaction.

``docs/threat-model.md`` classification rule 3: *redaction is allow-list, not
deny-list*. The logger emits only fields explicitly declared loggable. Anything else
is replaced with a redaction marker, so a newly added field is protected by default
rather than protected only if someone remembers to add it to a blocklist.

This implements SAFE-29. It is the reason a stray ``log.info("dose", amount=...)``
cannot leak a medication amount into a log file.
"""

from __future__ import annotations

import logging
from collections.abc import MutableMapping
from typing import Any, Final

import structlog

REDACTED: Final = "[redacted]"

#: Fields safe to emit. Everything here is class C0 (operational) or a bare
#: identifier -- never a health value, never a credential, never free text.
#: Adding a key to this set is a privacy decision: justify it against the data
#: classification table in docs/threat-model.md before doing so.
LOGGABLE_KEYS: Final[frozenset[str]] = frozenset(
    {
        # structlog / stdlib machinery
        "event",
        "level",
        "logger",
        "timestamp",
        "exc_info",
        "stack_info",
        # correlation and request shape (C0)
        "correlation_id",
        "request_id",
        "method",
        "path",
        "route",
        "status_code",
        "duration_ms",
        # identifiers, never their contents (C1/C2 IDs are opaque UUIDs)
        "owner_id",
        "event_id",
        "draft_id",
        "report_id",
        "job_id",
        "batch_id",
        "analysis_id",
        "episode_id",
        # operational metadata (C0)
        "job",
        "task",
        "attempt",
        "max_attempts",
        "queue_age_s",
        "outcome",
        "reason_code",
        "count",
        "created",
        "updated",
        "skipped",
        # integration metadata -- provider name and counts only, never payloads (C5/C8)
        "provider",
        "integration",
        "sync_window_start",
        "sync_window_end",
        # model metadata only -- never prompts or completions (C9)
        "model_name",
        "model_digest",
        "prompt_version",
        "schema_version",
        "latency_ms",
        "schema_valid",
    }
)


def redact_unlisted(
    _logger: Any,
    _method_name: str,
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Replace the value of every key not in :data:`LOGGABLE_KEYS`.

    The key itself is kept so that a redaction is visible in the log -- an operator
    can see that a field was dropped without seeing what it held.
    """
    for key in list(event_dict):
        if key not in LOGGABLE_KEYS:
            event_dict[key] = REDACTED
    return event_dict


def configure_logging(*, json_output: bool = True, level: int = logging.INFO) -> None:
    """Install the redacting structlog pipeline. Call once at startup."""
    renderer: Any = (
        structlog.processors.JSONRenderer()
        if json_output
        else structlog.dev.ConsoleRenderer(colors=False)
    )
    structlog.configure(
        processors=[
            structlog.contextvars.merge_contextvars,
            structlog.processors.add_log_level,
            structlog.processors.TimeStamper(fmt="iso", utc=True),
            # Redaction runs last so nothing added by an earlier processor escapes.
            redact_unlisted,
            renderer,
        ],
        wrapper_class=structlog.make_filtering_bound_logger(level),
        # Keep logger proxies reconfigurable.  The API, workers, CLI, and tests can
        # install the same privacy pipeline at different process entry points; a
        # proxy cached by the first entry point would otherwise retain a stale
        # processor chain and could bypass a later capture or renderer.
        cache_logger_on_first_use=False,
    )


def get_logger(name: str) -> Any:
    return structlog.get_logger(name)
