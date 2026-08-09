"""Write or verify the deterministic frontend OpenAPI contract."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from healthcurve.app import create_app
from healthcurve.config import Environment, Settings

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
SCHEMA_PATH = REPOSITORY_ROOT / "frontend" / "openapi.json"


def rendered_schema() -> str:
    """Return a stable development schema without consulting local secrets or .env."""
    settings = Settings.model_validate(
        {
            "environment": Environment.DEV,
            "ollama_base_url": "http://127.0.0.1:11434",
        }
    )
    return json.dumps(create_app(settings).openapi(), indent=2, sort_keys=True) + "\n"


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args()
    expected = rendered_schema()

    if args.check:
        if not SCHEMA_PATH.exists() or SCHEMA_PATH.read_text() != expected:
            print("OpenAPI contract is stale. Run `make frontend-generate` and commit it.")
            return 1
        return 0

    SCHEMA_PATH.write_text(expected)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
