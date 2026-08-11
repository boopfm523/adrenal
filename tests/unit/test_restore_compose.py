from __future__ import annotations

from pathlib import Path
from typing import Any

import yaml


def _compose() -> dict[str, Any]:
    root = Path(__file__).resolve().parents[2]
    return yaml.safe_load((root / "deploy/restore-drill.compose.yml").read_text(encoding="utf-8"))


def test_restore_stack_is_standalone_internal_and_has_no_persistent_database() -> None:
    compose = _compose()
    assert set(compose["services"]) == {"restore-postgres", "restore-runner"}
    assert compose["networks"]["restore-internal"]["internal"] is True
    assert "volumes" not in compose

    for service in compose["services"].values():
        assert "ports" not in service
        assert set(service["networks"]) == {"restore-internal"}
        assert service["restart"] == "no"

    database = compose["services"]["restore-postgres"]
    assert database["read_only"] is True
    assert any(value.startswith("/var/lib/postgresql/data:") for value in database["tmpfs"])
    assert any(value.startswith("/var/run/postgresql:") for value in database["tmpfs"])
    assert database["cap_drop"] == ["ALL"]
    assert database["security_opt"] == ["no-new-privileges:true"]


def test_restore_runner_mounts_only_encrypted_input_and_identity_read_only() -> None:
    runner = _compose()["services"]["restore-runner"]
    assert runner["read_only"] is True
    assert runner["cap_drop"] == ["ALL"]
    assert runner["security_opt"] == ["no-new-privileges:true"]
    assert any(value.startswith("/work:") for value in runner["tmpfs"])
    volumes = {entry["target"]: entry for entry in runner["volumes"]}
    assert set(volumes) == {"/input", "/run/secrets/age-identity"}
    assert all(entry["read_only"] is True for entry in volumes.values())
    assert "IDENTITY" not in " ".join(runner["environment"].values())
    assert "@restore-postgres:" in runner["environment"]["HC_RESTORE_DATABASE_URL"]
    assert "@restore-postgres:" in runner["environment"]["HC_RESTORE_AI_DATABASE_URL"]
