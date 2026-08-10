"""The in-app instructions must describe only implemented commands and routes."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from healthcurve.app import create_app
from healthcurve.config import Settings
from healthcurve.integrations.telegram.handlers import SUPPORTED_TELEGRAM_COMMANDS

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = ROOT / "frontend" / "src" / "helpContent.json"


def _manifest() -> dict[str, Any]:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def test_every_implemented_telegram_command_is_documented_once() -> None:
    entries = _manifest()["telegramCommands"]
    documented = [entry["command"].removeprefix("/") for entry in entries]

    assert len(documented) == len(set(documented))
    assert set(documented) == SUPPORTED_TELEGRAM_COMMANDS


def test_every_documented_api_workflow_route_exists_with_the_stated_method() -> None:
    settings = Settings(_env_file=None, ollama_base_url="http://ollama:11434")  # type: ignore[call-arg]
    paths = create_app(settings).openapi()["paths"]
    workflows = _manifest()["apiWorkflows"] + _manifest()["importWorkflows"]

    for workflow in workflows:
        assert workflow["endpoints"], workflow["id"]
        for endpoint in workflow["endpoints"]:
            path = endpoint["path"]
            method = endpoint["method"].lower()
            assert path in paths, f"{workflow['id']} advertises missing route {path}"
            assert method in paths[path], f"{workflow['id']} advertises missing {method} {path}"


def test_help_examples_are_synthetic_and_never_contain_credentials() -> None:
    manifest = _manifest()
    examples = [entry["example"] for entry in manifest["telegramCommands"]]
    examples.extend(
        workflow["example"] for workflow in manifest["apiWorkflows"] + manifest["importWorkflows"]
    )
    serialized = json.dumps(examples).lower()

    assert "password" not in serialized
    assert "bearer " not in serialized
    assert re.search(r"\d{8,10}:[A-Za-z0-9_-]{30,}", serialized) is None
    assert "@gmail.com" not in serialized
    for workflow in manifest["apiWorkflows"] + manifest["importWorkflows"]:
        assert "synthetic" in workflow["example"].lower()
