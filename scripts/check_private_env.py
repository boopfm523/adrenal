#!/usr/bin/env python3
"""Check the private owner runtime's .env without printing secret values."""

from __future__ import annotations

import argparse
import os
import stat
from pathlib import Path

_PLACEHOLDERS = {
    "changeme",
    "development",
    "dev-password",
    "healthcurve",
    "password",
    "replace-me",
    "secret",
    "synthetic-placeholder",
}


def _assignments(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("export "):
            line = line.removeprefix("export ").lstrip()
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in {'"', "'"}:
            value = value[1:-1]
        values[key.strip()] = value
    return values


def check(path: Path) -> list[str]:
    failures: list[str] = []
    if path.is_symlink():
        return ["env_is_symlink"]
    if not path.is_file():
        return ["env_missing"]

    metadata = path.stat()
    if metadata.st_uid != os.getuid():
        failures.append("env_not_owned_by_current_user")
    if stat.S_IMODE(metadata.st_mode) & 0o077:
        failures.append("env_permissions_not_owner_only")

    try:
        values = _assignments(path)
    except (OSError, UnicodeError):
        return [*failures, "env_unreadable"]

    password = values.get("POSTGRES_PASSWORD", "")
    ai_password = values.get("POSTGRES_AI_PASSWORD", "")
    if not password:
        failures.append("database_password_missing")
    if not ai_password:
        failures.append("ai_database_password_missing")
    if password and ai_password and password == ai_password:
        failures.append("database_role_passwords_not_distinct")
    if password.lower() in _PLACEHOLDERS or ai_password.lower() in _PLACEHOLDERS:
        failures.append("database_password_is_placeholder")
    if password and len(password) < 20:
        failures.append("database_password_too_short")
    if ai_password and len(ai_password) < 20:
        failures.append("ai_database_password_too_short")

    if values.get("HC_DEBUG", "false").strip().lower() not in {"0", "false", "no", "off"}:
        failures.append("debug_enabled")
    if values.get("HC_ENVIRONMENT", "dev").strip().lower() not in {"dev", "prod"}:
        failures.append("private_runtime_environment_invalid")
    return failures


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate private runtime environment controls without displaying values."
    )
    parser.add_argument("path", nargs="?", type=Path, default=Path(".env"))
    args = parser.parse_args()
    failures = check(args.path)
    for failure in failures:
        print(f"ERROR: {failure}")
    if failures:
        return 1
    print("private env ok: owner-only, debug off, distinct non-placeholder database roles")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
