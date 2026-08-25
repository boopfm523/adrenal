"""Generate ADR-0029's ignored public static data tree."""

from __future__ import annotations

import argparse
from pathlib import Path

from sqlalchemy import select

from healthcurve.db import get_session_factory
from healthcurve.identity.models import Owner
from healthcurve.public_site.exporter import export_public_data


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    with get_session_factory()() as session:
        owner = session.scalar(select(Owner).limit(1))
        if owner is None:
            parser.error("HealthCurve owner does not exist")
        manifest = export_public_data(session, owner=owner, output_directory=args.output)
    print(f"public_data_ready dates={len(manifest['dates'])} newest={manifest['newest_date']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
