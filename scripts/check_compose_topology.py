#!/usr/bin/env python3
"""Validate a rendered Compose topology without printing its environment.

Input must be `docker compose config --format json` output. Environment values may
contain secrets, so failures report only service/field names and safe network facts.
"""

from __future__ import annotations

import argparse
import ipaddress
import json
from pathlib import Path
from typing import Any

TAILNET = ipaddress.ip_network("100.64.0.0/10")


def validate(compose: dict[str, Any], *, production: bool) -> list[str]:
    errors: list[str] = []
    services = compose.get("services", {})

    publishers = [name for name, service in services.items() if service.get("ports")]
    if publishers != ["caddy"]:
        errors.append("only caddy may publish ports")

    caddy = services.get("caddy", {})
    ports = caddy.get("ports", [])
    if len(ports) != 1:
        errors.append("caddy must publish exactly one port")
    else:
        binding = ports[0]
        host = binding.get("host_ip") if isinstance(binding, dict) else None
        published = str(binding.get("published")) if isinstance(binding, dict) else ""
        target = str(binding.get("target")) if isinstance(binding, dict) else ""
        try:
            address = ipaddress.ip_address(host or "")
        except ValueError:
            errors.append("caddy must bind an explicit literal host IP")
        else:
            if production and address not in TAILNET:
                errors.append("production caddy must bind a Tailscale IPv4")
            if not production and not address.is_loopback:
                errors.append("development caddy must bind loopback")
        expected = "443" if production else None
        if expected and (published != expected or target != expected):
            errors.append("production caddy must publish 443 to container port 443")

    for name in ("postgres", "redis", "ollama", "api", "worker", "document-worker"):
        if services.get(name, {}).get("ports"):
            errors.append(f"{name} must not publish ports")

    documents = services.get("document-worker", {})
    if documents.get("network_mode") != "none":
        errors.append("document-worker must have no network")
    if documents.get("read_only") is not True:
        errors.append("document-worker must be read-only")
    if "ALL" not in documents.get("cap_drop", []):
        errors.append("document-worker must drop all capabilities")
    if "no-new-privileges:true" not in documents.get("security_opt", []):
        errors.append("document-worker must set no-new-privileges")

    if production:
        for name in ("api", "worker"):
            environment = services.get(name, {}).get("environment", {})
            if environment.get("HC_ENVIRONMENT") != "prod":
                errors.append(f"{name} must run in the prod environment")
            if environment.get("HC_MFA_REQUIRED") != "true":
                errors.append(f"{name} must require MFA")

        volumes = caddy.get("volumes", [])
        config_mount = next(
            (mount for mount in volumes if mount.get("target") == "/etc/caddy/Caddyfile"),
            None,
        )
        cert_mount = next(
            (mount for mount in volumes if mount.get("target") == "/run/tailscale-certs"),
            None,
        )
        if not config_mount or config_mount.get("read_only") is not True:
            errors.append("production Caddyfile must be mounted read-only")
        if not cert_mount or cert_mount.get("read_only") is not True:
            errors.append("Tailscale certificates must be mounted read-only")

    return errors


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("config", type=Path)
    parser.add_argument("--production", action="store_true")
    args = parser.parse_args()
    compose = json.loads(args.config.read_text(encoding="utf-8"))
    errors = validate(compose, production=args.production)
    for error in errors:
        print(f"ERROR: {error}")
    if errors:
        return 1
    print(
        "topology ok: private production edge"
        if args.production
        else "topology ok: loopback development edge"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
