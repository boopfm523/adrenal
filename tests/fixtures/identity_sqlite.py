"""An in-memory stand-in for the identity schema.

The zone ledger is plain rows and a unique constraint, so SQLite is enough to test it
without a container. One difference matters: SQLite has no ``timestamptz``, so a
datetime *re-read from disk* comes back naive where PostgreSQL returns it aware.
Rather than leave that for each test to work around -- and let production code that
requires an aware instant pass here only because nothing reloaded the row -- the load
listener below puts UTC back on.
"""

from __future__ import annotations

import sqlite3
from datetime import UTC

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Mapper
from sqlalchemy.pool import StaticPool

from healthcurve.db import IdentityBase
from healthcurve.identity.models import Owner, TimezoneStay


@event.listens_for(TimezoneStay, "load", propagate=True)
def _restore_utc(target: TimezoneStay, _context: Mapper[TimezoneStay]) -> None:
    """Undo SQLite's dropped offset on load.

    A no-op against PostgreSQL, where the value already arrives aware, so importing
    this module cannot change what an integration test observes.
    """
    for field in ("started_at", "created_at"):
        value = getattr(target, field, None)
        if value is not None and value.tzinfo is None:
            setattr(target, field, value.replace(tzinfo=UTC))


def identity_engine() -> Engine:
    """An engine carrying only the owner and timezone-stay tables.

    Deliberately not the whole metadata: it also holds JSONB columns that SQLite cannot
    compile, and nothing here touches them.
    """
    # One connection, shared: ``:memory:`` is per-connection, so a second connection
    # would open an empty database -- including the one a TestClient makes on its own
    # thread, which is why this is StaticPool and not the default.
    engine = create_engine(
        "sqlite://", poolclass=StaticPool, connect_args={"check_same_thread": False}
    )

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection: sqlite3.Connection, _record: object) -> None:
        # SQLite has no schemas, and a connection without the ATTACH cannot see the
        # table at all.
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS identity")

    IdentityBase.metadata.create_all(
        engine,
        tables=[
            IdentityBase.metadata.tables[f"identity.{table}"]
            for table in (Owner.__tablename__, TimezoneStay.__tablename__)
        ],
    )
    return engine
