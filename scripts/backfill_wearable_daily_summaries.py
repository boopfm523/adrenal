#!/usr/bin/env python3
"""Idempotently backfill versioned wearable daily summaries in bounded commits."""

from __future__ import annotations

import argparse
import os
from datetime import date, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.analytics import wearable_summaries
from healthcurve.db import build_engine
from healthcurve.identity.models import Owner


def _date(value: str) -> date:
    try:
        return date.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected YYYY-MM-DD") from exc


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("HC_DATABASE_URL"))
    parser.add_argument("--date-from", type=_date, required=True)
    parser.add_argument("--date-to", type=_date, required=True)
    parser.add_argument(
        "--chunk-days",
        type=int,
        default=wearable_summaries.MAX_RAW_CHUNK_DAYS,
        choices=range(1, wearable_summaries.MAX_RAW_CHUNK_DAYS + 1),
        metavar=f"1-{wearable_summaries.MAX_RAW_CHUNK_DAYS}",
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or HC_DATABASE_URL is required")
    if args.date_to < args.date_from:
        parser.error("--date-to must not precede --date-from")

    engine = build_engine(args.database_url)
    try:
        with Session(engine) as session:
            owners = list(session.execute(select(Owner.id, Owner.default_timezone)))
        chunk_count = 0
        for owner_id, timezone in owners:
            cursor = args.date_from
            while cursor <= args.date_to:
                chunk_end = min(args.date_to, cursor + timedelta(days=args.chunk_days - 1))
                with Session(engine) as session, session.begin():
                    wearable_summaries.ensure_daily_summaries(
                        session,
                        owner_id=owner_id,
                        date_from=cursor,
                        date_to=chunk_end,
                        timezone=timezone,
                    )
                chunk_count += 1
                cursor = chunk_end + timedelta(days=1)
        print(
            f"wearable summary backfill complete: owners={len(owners)}; "
            f"chunks={chunk_count}; version={wearable_summaries.SUMMARY_VERSION}"
        )
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
