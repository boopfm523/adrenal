"""The extraction path connects as its own role (SAFE-15, SAFE-16).

The rule is a database privilege, and a privilege only protects you if the code
actually opens a connection that holds it. An earlier build applied the restriction
per *process* -- the whole worker ran as the AI role -- which was the wrong boundary:
the worker also performs the owner's confirmation, so the restriction blocked the
human it was meant to protect while the model kept writing drafts through the same
connection anyway.

These tests pin the boundary itself: privileged by default, restricted for the one
write that carries model output.
"""

from __future__ import annotations

from typing import Any

import pytest

from healthcurve.config import Settings
from healthcurve.db import (
    build_ai_engine,
    build_engine,
    get_ai_engine,
    get_ai_session_factory,
    get_engine,
    get_session_factory,
)

PRIVILEGED = "postgresql+psycopg://healthcurve:pw@postgres:5432/healthcurve"
RESTRICTED = "postgresql+psycopg://healthcurve_ai:pw@postgres:5432/healthcurve"


def _settings(**overrides: Any) -> Settings:
    base: dict[str, Any] = {
        "_env_file": None,
        "ollama_base_url": "http://ollama:11434",
        "database_url": PRIVILEGED,
        "ai_database_url": RESTRICTED,
    }
    base.update(overrides)
    return Settings(**base)


@pytest.fixture(autouse=True)
def _clear_engine_caches() -> Any:
    """Both factories are lru_cached; a leaked cache would make these vacuous."""
    for fn in (get_engine, get_ai_engine, get_session_factory, get_ai_session_factory):
        fn.cache_clear()
    yield
    for fn in (get_engine, get_ai_engine, get_session_factory, get_ai_session_factory):
        fn.cache_clear()


def test_the_two_engines_use_different_roles() -> None:
    settings = _settings()
    assert build_engine(settings.database_url).url.username == "healthcurve"
    assert build_ai_engine(settings).url.username == "healthcurve_ai"


def test_unset_ai_url_falls_back_but_says_so() -> None:
    """Silence here would be the worst outcome: the guarantee gone, nothing to see."""
    from structlog.testing import capture_logs

    settings = _settings(ai_database_url=None)
    with capture_logs() as logs:
        engine = build_ai_engine(settings)

    assert engine.url.username == "healthcurve"
    assert any(entry.get("reason_code") == "ai_role_not_separated" for entry in logs)


def _source_of(function_name: str) -> str:
    """Source of a handler, fetched by name so the test does not import a private."""
    import inspect

    from healthcurve.integrations.telegram import handlers

    return inspect.getsource(getattr(handlers, function_name))


def test_free_text_drafts_are_written_through_the_ai_session() -> None:
    """The one write carrying model output must not use the caller's session.

    Asserted against the source because the alternative -- a live two-role database --
    belongs in the integration suite, and this is the property that regressed.
    """
    assert "get_ai_session_factory" in _source_of("_handle_free_text"), (
        "free-text drafts must be persisted through the restricted role"
    )


def test_command_paths_do_not_use_the_ai_session() -> None:
    """/dose and /symptom are deterministic, not model output. Routing them through
    the restricted role would imply the AI produced them."""
    for name in ("_cmd_dose", "_cmd_symptom", "_cmd_injection"):
        assert "get_ai_session_factory" not in _source_of(name)


def test_chat_worker_keeps_identity_out_of_the_ai_role() -> None:
    """Chat output is restricted, but profile timezone lookup is not an AI read."""
    import inspect

    from healthcurve import worker
    from healthcurve.chat import jobs

    wiring = inspect.getsource(worker.main)
    handler = inspect.getsource(jobs.make_chat_response_handler)

    assert "get_ai_session_factory()" in wiring
    assert "identity_factory=get_session_factory()" in wiring
    assert "with identity_factory() as identity_session" in handler
    assert "with factory() as failure_session" in handler
    assert 'failed.error_code = "chat_worker_failed"' in handler
