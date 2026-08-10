"""Validate the private rclone Drive configuration without disclosing its contents."""

from __future__ import annotations

import argparse
import configparser
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any

REMOTE_NAME = re.compile(r"^[a-z][a-z0-9-]{0,62}$")
GOOGLE_CLIENT_ID = re.compile(r"^[A-Za-z0-9._-]+\.apps\.googleusercontent\.com$")


def check_config(path: Path, remote: str = "healthcurve-drive") -> list[str]:
    """Return fixed reason codes; never include paths, values, or parser exceptions."""
    failures: list[str] = []
    if not path.is_absolute():
        failures.append("config_path_not_absolute")
    try:
        metadata = path.lstat()
    except OSError:
        return [*failures, "config_unavailable"]
    if stat.S_ISLNK(metadata.st_mode):
        return [*failures, "config_is_symlink"]
    if not stat.S_ISREG(metadata.st_mode):
        return [*failures, "config_not_regular_file"]
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        failures.append("config_permissions_not_owner_only")
    if REMOTE_NAME.fullmatch(remote) is None:
        return [*failures, "remote_name_invalid"]

    parser = configparser.RawConfigParser(interpolation=None, strict=True)
    try:
        with path.open(encoding="utf-8") as stream:
            parser.read_file(stream)
    except (OSError, UnicodeError, configparser.Error):
        return [*failures, "config_parse_failed"]
    if not parser.has_section(remote):
        return [*failures, "remote_missing"]

    def get(key: str) -> str:
        return parser.get(remote, key, fallback="").strip()

    if get("type") != "drive":
        failures.append("remote_type_not_drive")
    client_id = get("client_id")
    if not client_id:
        failures.append("owner_client_id_missing")
    elif GOOGLE_CLIENT_ID.fullmatch(client_id) is None:
        failures.append("owner_client_id_invalid")
    if not get("client_secret"):
        failures.append("owner_client_secret_missing")
    if get("scope") != "drive.file":
        failures.append("scope_not_drive_file")
    if get("service_account_file"):
        failures.append("unexpected_service_account")

    token = get("token")
    if not token:
        failures.append("oauth_token_missing")
    else:
        try:
            token_data: Any = json.loads(token)
        except json.JSONDecodeError:
            failures.append("oauth_token_invalid")
        else:
            if (
                not isinstance(token_data, dict)
                or not isinstance(token_data.get("refresh_token"), str)
                or not token_data["refresh_token"]
            ):
                failures.append("oauth_refresh_token_missing")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check an owner-controlled HealthCurve rclone Drive client safely."
    )
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--remote", default="healthcurve-drive")
    args = parser.parse_args(argv)
    failures = check_config(args.config, args.remote)
    for failure in failures:
        print(f"rclone oauth config failure: {failure}", file=sys.stderr)
    if failures:
        return 1
    print(
        "rclone oauth config: owner client, drive.file scope, refresh token, "
        "and file protections verified"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
