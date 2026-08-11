from __future__ import annotations

from copy import deepcopy
from pathlib import Path
from typing import Any

import yaml
from scripts.check_compose_topology import validate


def _config(*, production: bool) -> dict[str, Any]:
    host = "100.64.0.10" if production else "127.0.0.1"
    port = 443 if production else 8080
    target = 443 if production else 80
    environment = {"HC_ENVIRONMENT": "prod" if production else "dev"}
    return {
        "services": {
            "caddy": {
                "ports": [{"host_ip": host, "published": str(port), "target": target}],
                "volumes": [
                    {"target": "/etc/caddy/Caddyfile", "read_only": True},
                    {"target": "/run/tailscale-certs", "read_only": True},
                ],
            },
            "api": {"environment": deepcopy(environment)},
            "worker": {"environment": deepcopy(environment)},
            "cleanup-worker": {
                "environment": deepcopy(environment),
                "networks": {"hc-cleanup": None},
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
                "volumes": [
                    {"target": "/data/uploads"},
                    {"target": "/data/reports"},
                ],
            },
            "postgres": {"networks": {"hc-internal": None, "hc-cleanup": None, "hc-garmin": None}},
            "redis": {},
            "ollama": {"profiles": ["container-ollama"]},
            "document-worker": {
                "network_mode": "none",
                "read_only": True,
                "cap_drop": ["ALL"],
                "security_opt": ["no-new-privileges:true"],
            },
        },
        "networks": {"hc-cleanup": {"internal": True}, "hc-garmin": {}},
    }


def test_accepts_loopback_development_and_tailnet_production() -> None:
    assert validate(_config(production=False), production=False) == []
    assert validate(_config(production=True), production=True) == []


def test_base_compose_defaults_to_host_native_ollama() -> None:
    root = Path(__file__).resolve().parents[2]
    compose = yaml.safe_load((root / "docker-compose.yml").read_text(encoding="utf-8"))
    services = compose["services"]
    assert services["ollama"]["profiles"] == ["container-ollama"]
    for name in ("api", "worker"):
        endpoint = services[name]["environment"]["HC_OLLAMA_BASE_URL"]
        assert "host.docker.internal:11434" in endpoint


def test_base_topology_preserves_the_garmin_database_path_without_the_worker() -> None:
    config = _config(production=False)
    assert "garmin-worker" not in config["services"]
    assert validate(config, production=False) == []

    config["services"]["postgres"]["networks"].pop("hc-garmin")
    assert (
        "postgres must always join hc-garmin so routine updates preserve worker access"
        in validate(config, production=False)
    )


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
    config["services"]["worker"]["environment"]["HC_ENVIRONMENT"] = "dev"
    errors = validate(config, production=True)
    assert "worker must run in the prod environment" in errors


def test_rejects_cleanup_worker_external_access_or_credential_expansion() -> None:
    config = deepcopy(_config(production=False))
    cleanup = config["services"]["cleanup-worker"]
    cleanup["networks"] = {"hc-internal": None}
    cleanup["environment"]["HC_TELEGRAM_BOT_TOKEN"] = "synthetic-placeholder"
    config["networks"]["hc-cleanup"]["internal"] = False
    config["services"]["postgres"]["networks"] = {"hc-internal": None}
    errors = validate(config, production=False)
    assert "cleanup-worker must use only hc-cleanup" in errors
    assert "cleanup-worker must not receive HC_TELEGRAM_BOT_TOKEN" in errors
    assert "hc-cleanup network must be internal-only" in errors
    assert "postgres must join hc-cleanup for cleanup-worker database access" in errors


def test_accepts_isolated_garmin_worker_and_rejects_secret_expansion() -> None:
    config = deepcopy(_config(production=False))
    config["services"]["garmin-worker"] = {
        "environment": {
            "HC_DATABASE_URL": "synthetic-placeholder",
            "HC_GARMIN_ENABLED": "true",
            "HC_GARMIN_TOKEN_STORE": "/run/secrets/garmin",
        },
        "networks": {"hc-garmin": None},
        "read_only": True,
        "cap_drop": ["ALL"],
        "security_opt": ["no-new-privileges:true"],
        "volumes": [{"target": "/run/secrets/garmin"}],
    }
    assert validate(config, production=False) == []

    config["services"]["garmin-worker"]["environment"]["HC_TELEGRAM_BOT_TOKEN"] = (
        "synthetic-placeholder"
    )
    config["services"]["garmin-worker"]["networks"] = {"hc-internal": None}
    config["services"]["garmin-worker"]["volumes"].append({"target": "/data/uploads"})
    errors = validate(config, production=False)
    assert "garmin-worker must use only hc-garmin" in errors
    assert "garmin-worker may mount only its token directory" in errors
    assert "garmin-worker must not receive HC_TELEGRAM_BOT_TOKEN" in errors
