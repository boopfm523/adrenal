from __future__ import annotations

from copy import deepcopy
from typing import Any

from scripts.check_compose_topology import validate


def _config(*, production: bool) -> dict[str, Any]:
    host = "100.64.0.10" if production else "127.0.0.1"
    port = 443 if production else 8080
    target = 443 if production else 80
    environment = {
        "HC_ENVIRONMENT": "prod" if production else "dev",
        "HC_MFA_REQUIRED": "true" if production else "false",
    }
    return {
        "services": {
            "caddy": {
                "ports": [{"host_ip": host, "published": str(port), "target": target}],
                "volumes": [
                    {"target": "/etc/caddy/Caddyfile", "read_only": True},
                    {"target": "/run/tailscale-certs", "read_only": True},
                ],
            },
            "api": {"environment": environment},
            "worker": {"environment": environment},
            "postgres": {},
            "redis": {},
            "ollama": {},
            "document-worker": {
                "network_mode": "none",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
            },
        }
    }


def test_accepts_loopback_development_and_tailnet_production() -> None:
    assert validate(_config(production=False), production=False) == []
    assert validate(_config(production=True), production=True) == []


def test_rejects_public_or_lan_production_binding() -> None:
    config = _config(production=True)
    config["services"]["caddy"]["ports"][0]["host_ip"] = "192.168.1.5"
    assert "production caddy must bind a Tailscale IPv4" in validate(config, production=True)


def test_rejects_a_second_publisher_and_writable_certificates() -> None:
    config = _config(production=True)
    config["services"]["postgres"]["ports"] = [{"published": "5432"}]
    config["services"]["caddy"]["volumes"][1]["read_only"] = False
    errors = validate(config, production=True)
    assert "only caddy may publish ports" in errors
    assert "postgres must not publish ports" in errors
    assert "Tailscale certificates must be mounted read-only" in errors


def test_rejects_non_production_application_policy() -> None:
    config = deepcopy(_config(production=True))
    config["services"]["api"]["environment"]["HC_MFA_REQUIRED"] = "false"
    config["services"]["worker"]["environment"]["HC_ENVIRONMENT"] = "dev"
    errors = validate(config, production=True)
    assert "api must require MFA" in errors
    assert "worker must run in the prod environment" in errors
