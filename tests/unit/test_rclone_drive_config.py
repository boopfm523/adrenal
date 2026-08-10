from __future__ import annotations

from pathlib import Path

import pytest
from scripts.check_rclone_drive_config import check_config, main


def _config(
    path: Path,
    *,
    client_id: str = "synthetic.apps.googleusercontent.com",
    scope: str = "drive.file",
    token: str | None = None,
) -> Path:
    token = token or '{"refresh_token":"synthetic-refresh"}'
    path.write_text(
        "\n".join(
            (
                "[healthcurve-drive]",
                "type = drive",
                f"client_id = {client_id}",
                "client_secret = synthetic-obscured-secret",
                f"scope = {scope}",
                f"token = {token}",
                "service_account_file =",
                "",
            )
        ),
        encoding="utf-8",
    )
    path.chmod(0o600)
    return path


def test_owner_client_drive_file_config_passes(tmp_path: Path) -> None:
    assert check_config(_config(tmp_path / "rclone.conf")) == []


def test_shared_client_and_broad_scope_fail(tmp_path: Path) -> None:
    failures = check_config(_config(tmp_path / "rclone.conf", client_id="", scope="drive"))
    assert failures == ["owner_client_id_missing", "scope_not_drive_file"]


def test_insecure_permissions_and_symlink_fail(tmp_path: Path) -> None:
    config = _config(tmp_path / "rclone.conf")
    config.chmod(0o640)
    assert "config_permissions_not_owner_only" in check_config(config)
    link = tmp_path / "linked.conf"
    link.symlink_to(config)
    assert "config_is_symlink" in check_config(link)


def test_malformed_secret_is_not_disclosed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    sentinel = "do-not-disclose-this-value"
    config = _config(tmp_path / "rclone.conf", token=sentinel)
    assert main(["--config", str(config)]) == 1
    captured = capsys.readouterr()
    assert "oauth_token_invalid" in captured.err
    assert sentinel not in captured.err
    assert sentinel not in captured.out


def test_missing_remote_fails_with_fixed_reason(tmp_path: Path) -> None:
    path = tmp_path / "rclone.conf"
    path.write_text("[another-remote]\ntype = drive\n", encoding="utf-8")
    path.chmod(0o600)
    assert check_config(path) == ["remote_missing"]
