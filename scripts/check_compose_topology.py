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

    for name in (
        "postgres",
        "redis",
        "ollama",
        "api",
        "worker",
        "document-worker",
        "cleanup-worker",
    ):
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

    cleanup = services.get("cleanup-worker", {})
    if cleanup.get("read_only") is not True:
        errors.append("cleanup-worker must be read-only")
    if "ALL" not in cleanup.get("cap_drop", []):
        errors.append("cleanup-worker must drop all capabilities")
    if "no-new-privileges:true" not in cleanup.get("security_opt", []):
        errors.append("cleanup-worker must set no-new-privileges")
    cleanup_networks = cleanup.get("networks", {})
    cleanup_network_names = (
        set(cleanup_networks) if isinstance(cleanup_networks, (dict, list)) else set()
    )
    if cleanup_network_names != {"hc-cleanup"}:
        errors.append("cleanup-worker must use only hc-cleanup")
    if services.get("worker", {}).get("volumes"):
        worker_targets = {
            mount.get("target")
            for mount in services["worker"]["volumes"]
            if isinstance(mount, dict)
        }
        if {"/data/uploads", "/data/reports"} & worker_targets:
            errors.append("worker must not mount private document or report storage")
    cleanup_environment = cleanup.get("environment", {})
    for forbidden in (
        "HC_TELEGRAM_BOT_TOKEN",
        "HC_TELEGRAM_WEBHOOK_SECRET",
        "HC_OLLAMA_BASE_URL",
        "HC_REDIS_URL",
    ):
        if forbidden in cleanup_environment:
            errors.append(f"cleanup-worker must not receive {forbidden}")
    cleanup_network = compose.get("networks", {}).get("hc-cleanup", {})
    if cleanup_network.get("internal") is not True:
        errors.append("hc-cleanup network must be internal-only")
    postgres_networks = services.get("postgres", {}).get("networks", {})
    postgres_network_names = (
        set(postgres_networks) if isinstance(postgres_networks, (dict, list)) else set()
    )
    if "hc-cleanup" not in postgres_network_names:
        errors.append("postgres must join hc-cleanup for cleanup-worker database access")

    garmin = services.get("garmin-worker")
    if isinstance(garmin, dict):
        if garmin.get("read_only") is not True:
            errors.append("garmin-worker must be read-only")
        if "ALL" not in garmin.get("cap_drop", []):
            errors.append("garmin-worker must drop all capabilities")
        if "no-new-privileges:true" not in garmin.get("security_opt", []):
            errors.append("garmin-worker must set no-new-privileges")
        garmin_networks = garmin.get("networks", {})
        garmin_network_names = (
            set(garmin_networks) if isinstance(garmin_networks, (dict, list)) else set()
        )
        if garmin_network_names != {"hc-garmin"}:
            errors.append("garmin-worker must use only hc-garmin")
        garmin_targets = {
            mount.get("target") for mount in garmin.get("volumes", []) if isinstance(mount, dict)
        }
        if garmin_targets != {"/run/secrets/garmin"}:
            errors.append("garmin-worker may mount only its token directory")
        garmin_environment = garmin.get("environment", {})
        for forbidden in (
            "HC_AI_DATABASE_URL",
            "HC_BEADS_OUTBOX_DIR",
            "HC_CREDENTIAL_KEY_FILE",
            "HC_GARMIN_EMAIL",
            "HC_GARMIN_PASSWORD",
            "HC_OLLAMA_BASE_URL",
            "HC_REDIS_URL",
            "HC_REPORT_ARTIFACTS_DIR",
            "HC_TELEGRAM_BOT_TOKEN",
            "HC_TELEGRAM_WEBHOOK_SECRET",
            "HC_UPLOADS_DIR",
        ):
            if forbidden in garmin_environment:
                errors.append(f"garmin-worker must not receive {forbidden}")
        if "hc-garmin" not in postgres_network_names:
            errors.append("postgres must join hc-garmin for Garmin worker database access")

    if production:
        for name in ("api", "worker", "cleanup-worker"):
            environment = services.get(name, {}).get("environment", {})
            if environment.get("HC_ENVIRONMENT") != "prod":
                errors.append(f"{name} must run in the prod environment")

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
