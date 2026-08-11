from __future__ import annotations

import os
from pathlib import Path

import pytest
from scripts.check_private_env import check, main


def _write(path: Path, content: str, mode: int = 0o600) -> Path:
    path.write_text(content, encoding="utf-8")
    path.chmod(mode)
    return path


def test_private_env_accepts_owner_only_distinct_secrets_and_dev_runtime(tmp_path: Path) -> None:
    path = _write(
        tmp_path / ".env",
        "\n".join(
            (
                "HC_ENVIRONMENT=dev",
                "HC_DEBUG=false",
                f"POSTGRES_PASSWORD={'a' * 24}",
                f"POSTGRES_AI_PASSWORD={'b' * 24}",
            )
        ),
    )
    assert check(path) == []


def test_private_env_rejects_unsafe_permissions_debug_and_shared_roles(tmp_path: Path) -> None:
    path = _write(
        tmp_path / ".env",
        "HC_DEBUG=true\nPOSTGRES_PASSWORD=password\nPOSTGRES_AI_PASSWORD=password\n",
        mode=0o644,
    )
    assert set(check(path)) == {
        "env_permissions_not_owner_only",
        "database_role_passwords_not_distinct",
        "database_password_is_placeholder",
        "database_password_too_short",
        "ai_database_password_too_short",
        "debug_enabled",
    }


def test_private_env_rejects_symlink_without_reading_target(tmp_path: Path) -> None:
    target = _write(tmp_path / "target", f"POSTGRES_PASSWORD={'a' * 24}\n")
    link = tmp_path / ".env"
    link.symlink_to(target)
    assert check(link) == ["env_is_symlink"]


def test_private_env_cli_prints_only_reason_codes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    path = _write(tmp_path / ".env", "POSTGRES_PASSWORD=do-not-print\n")
    monkeypatch.setattr("sys.argv", ["check_private_env.py", os.fspath(path)])
    assert main() == 1
    output = capsys.readouterr().out
    assert "do-not-print" not in output
    assert "ai_database_password_missing" in output


def test_release_checklist_names_every_required_gate() -> None:
    checklist = (
        Path(__file__).resolve().parents[2] / "docs" / "private-release-checklist.md"
    ).read_text(encoding="utf-8")
    for required in (
        "Alembic head",
        "Private secrets and debug-off",
        "No public listeners",
        "Encrypted backup health",
        "Isolated restore drill",
        "Security review",
        "Emergency page with optional services offline",
    ):
        assert required in checklist
    for executable in (
        "scripts/check_private_env.py",
        "scripts/check_compose_topology.py /dev/stdin",
        "python -m healthcurve.backup_status",
        "scripts/run_restore_drill.py",
        "test_emergency_page_renders_without_ai_or_javascript",
        "bd show hc-cbs.1",
    ):
        assert executable in checklist
