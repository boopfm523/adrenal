"""Activate a qualified text model or atomically restore the previous default."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from healthcurve.ai.model_selection import (
    ModelSelectionError,
    select_default_model,
    select_qualified_model,
)
from healthcurve.config import Settings

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_QUALIFICATION = ROOT / "evals" / "candidates" / "qwen3.8-27b-q8_0" / "qualification.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:11434")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    subparsers = parser.add_subparsers(dest="action", required=True)
    activate = subparsers.add_parser("activate")
    activate.add_argument("--qualification", type=Path, default=DEFAULT_QUALIFICATION)
    subparsers.add_parser("rollback")
    args = parser.parse_args()

    settings = Settings(ollama_base_url=args.base_url)
    try:
        if args.action == "activate":
            selected = select_qualified_model(
                qualification_path=args.qualification,
                env_path=args.env_file,
                settings=settings,
            )
        else:
            selected = select_default_model(env_path=args.env_file, settings=settings)
    except (ModelSelectionError, ValueError) as exc:
        print(f"model selection failed: {exc}", file=sys.stderr)
        return 1
    print(f"selected {selected}; recreate api and worker services to apply")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
