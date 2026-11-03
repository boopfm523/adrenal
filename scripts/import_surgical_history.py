#!/usr/bin/env python3
"""Idempotently import the owner's surgical/procedure history as life events.

Each entry becomes a ``LifeEvent`` with category ``surgery``: title is the
procedure, description is the facility and surgeon(s), and the local time is
noon on the given day (the exact time of day was never recorded). The
historical timezone is resolved per entry through the owner's recorded
timezone-stay history, the same backfill resolution the web app and Telegram
bot use for a stated wall time on a past day.

Safe to re-run: an entry already present (same owner, title, and local day)
is skipped rather than duplicated.
"""

from __future__ import annotations

import argparse
import os
import uuid
from dataclasses import dataclass
from datetime import date, datetime, time

from sqlalchemy import select
from sqlalchemy.orm import Session

from healthcurve.db import build_engine
from healthcurve.events import service as events
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.events.models import LifeEvent, LifeEventCategory
from healthcurve.identity import timezones
from healthcurve.identity.models import Owner


@dataclass(frozen=True, slots=True)
class ProcedureRecord:
    day: date
    title: str
    description: str


# Transcribed verbatim from the owner's own list, including original wording.
PROCEDURES: tuple[ProcedureRecord, ...] = (
    ProcedureRecord(
        date(2020, 2, 20),
        "Large margin surgical removal of BAPoma",
        "Johns Hopkins - Dr Elise Ng",
    ),
    ProcedureRecord(
        date(2020, 7, 7),
        "Large margin surgical removal of BAPoma",
        "Johns Hopkins - Dr Elise Ng",
    ),
    ProcedureRecord(
        date(2020, 7, 15),
        "Large margin surgical removal of BAPoma",
        "Johns Hopkins - Dr Sophia Strike",
    ),
    ProcedureRecord(
        date(2021, 1, 20),
        "Large margin surgical removal of melanoma",
        "Johns Hopkins - Dr Jeffrey Scott",
    ),
    ProcedureRecord(
        date(2021, 5, 17),
        "Large margin surgical removal of BAPoma",
        "Johns Hopkins - Dr Jeffrey Scott",
    ),
    ProcedureRecord(
        date(2022, 8, 8),
        "Large margin surgical removal of BAPoma",
        "Johns Hopkins - Dr Jeffrey Scott",
    ),
    ProcedureRecord(
        date(2023, 3, 3),
        "Robotic assisted left nephrecomy and left adrenalectomy",
        "Memorial Sloan Kettering - Dr Alvin Goh",
    ),
    ProcedureRecord(
        date(2023, 3, 30),
        "Large margin surgical removal of compound dysplastic nevus with moderate to focally "
        "severe atypia",
        "Johns Hopkins - Dr Kristin Bibee",
    ),
    ProcedureRecord(
        date(2023, 6, 14),
        "Bilateral Vericocealectomy and Left hydrocelectomy",
        "Johns Hopkins - Dr Amin Herati",
    ),
    ProcedureRecord(
        date(2024, 2, 20),
        "Large margin surgical removal of two melanomas",
        "Johns Hopkins - Dr Kristin Bibee",
    ),
    ProcedureRecord(
        date(2024, 2, 27),
        "Large margin surgical removal of a melanoma",
        "Johns Hopkins - Dr Kristin Bibee",
    ),
    ProcedureRecord(
        date(2024, 3, 19),
        "Large margin surgical removal of a melanoma",
        "Johns Hopkins - Dr Kristin Bibee",
    ),
    ProcedureRecord(
        date(2024, 7, 25),
        "Bilateral Video Assisted Thorasic Surgery",
        "National Institues of Health - Dr David Schrump",
    ),
    ProcedureRecord(
        date(2024, 8, 13),
        "Lapraroscopic Surgery on Peritoneum and Cystoscopy",
        "National Institues of Health - Dr Shamus Carr & Dr. Vladimir Valera",
    ),
    ProcedureRecord(
        date(2024, 11, 11),
        "Subladbial Transsphenoidal Surgery (TSS) - removal of ACTH secreating pituitary adonoma",
        "National Institues of Health - Dr Prashant Chittiboina",
    ),
    ProcedureRecord(
        date(2025, 2, 25),
        "Bilateral Video Assisted Thorasic Surgery and Lapraroscopic Surgery on Peritoneum",
        "National Institues of Health - Dr David Schrump & Dr Shamus Carr",
    ),
    ProcedureRecord(
        date(2025, 10, 9),
        "Bilateral Video Assisted Thorasic Surgery and Lapraroscopic Surgery on Peritoneum",
        "National Institues of Health - Dr David Schrump & Dr Shamus Carr",
    ),
    ProcedureRecord(
        date(2026, 1, 29),
        "Posterolateral thoracotomy with a plueralectomy, decortation and plication of the right "
        "diaphraghm",
        "National Institues of Health - Dr David Schrump",
    ),
    ProcedureRecord(
        date(2026, 6, 3),
        "Cystoscopy",
        "National Institues of Health - Dr Raju Chelluri",
    ),
    ProcedureRecord(
        date(2026, 8, 18),
        "Robot assisted right adrenalectomy",
        "National Institues of Health - Dr Sandeep Gurram",
    ),
)


def _existing_titles_by_day(session: Session, *, owner_id: uuid.UUID) -> set[tuple[date, str]]:
    rows = session.scalars(
        select(LifeEvent).where(
            LifeEvent.owner_id == owner_id,
            LifeEvent.category == LifeEventCategory.SURGERY,
            events.current_fact_predicate(LifeEvent, owner_id=owner_id),
        )
    )
    return {(row.local_time.date(), row.title) for row in rows}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--database-url", default=os.getenv("HC_DATABASE_URL"))
    parser.add_argument("--owner-email", help="Defaults to the sole owner row if omitted.")
    parser.add_argument(
        "--dry-run", action="store_true", help="Report what would be created; create nothing."
    )
    args = parser.parse_args()
    if not args.database_url:
        parser.error("--database-url or HC_DATABASE_URL is required")

    engine = build_engine(args.database_url)
    try:
        with Session(engine) as session:
            if args.owner_email:
                owner = session.scalar(select(Owner).where(Owner.email == args.owner_email))
                if owner is None:
                    parser.error(f"no owner with email {args.owner_email!r}")
            else:
                owners = session.scalars(select(Owner)).all()
                if len(owners) != 1:
                    parser.error("--owner-email is required when more than one owner row exists")
                owner = owners[0]

            existing = _existing_titles_by_day(session, owner_id=owner.id)
            created = 0
            skipped = 0
            for record in PROCEDURES:
                if (record.day, record.title) in existing:
                    skipped += 1
                    continue
                if args.dry_run:
                    created += 1
                    continue
                # Naive on purpose: a wall-clock local reading, resolved below.
                local_time = datetime.combine(record.day, time(12, 0))
                zone = timezones.zone_for_local_time(session, owner, local_time)
                event_time = events.build_event_time(local_time, zone)
                events.create_event(
                    session,
                    LifeEvent,
                    owner_id=owner.id,
                    event_time=event_time,
                    source_type=SourceType.CSV_IMPORT,
                    confirmation_state=ConfirmationState.DIRECT,
                    title=record.title,
                    category=LifeEventCategory.SURGERY,
                    description=record.description,
                    is_sensitive=False,
                )
                created += 1
            if not args.dry_run:
                session.commit()
        verb = "would create" if args.dry_run else "created"
        print(f"surgical history import complete: {verb}={created}; skipped(existing)={skipped}")
    finally:
        engine.dispose()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
