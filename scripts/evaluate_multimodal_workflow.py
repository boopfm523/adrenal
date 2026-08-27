"""Validate the approved all-synthetic image/PDF workflow contract."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from pydantic import ValidationError

from healthcurve.ai.multimodal_evaluation import (
    load_multimodal_gold,
    render_multimodal_summary,
    verify_multimodal_contract,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_GOLD = ROOT / "evals" / "vision" / "workflow-gold-v2.json"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--gold", type=Path, default=DEFAULT_GOLD)
    args = parser.parse_args()
    try:
        summary = verify_multimodal_contract(load_multimodal_gold(args.gold))
    except (OSError, ValueError, ValidationError) as exc:
        print(f"multimodal workflow evaluation failed: {exc}", file=sys.stderr)
        return 1
    print(render_multimodal_summary(summary), end="")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
