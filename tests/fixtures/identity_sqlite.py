"""An in-memory stand-in for the identity schema.

The zone ledger is plain rows and a unique constraint, so SQLite is enough to test it
without a container. One difference matters and is not worth hiding: SQLite has no
``timestamptz``, so a datetime *re-read from disk* comes back naive where PostgreSQL
returns it aware.

That only bites when SQLAlchemy's weakly-referenced identity map drops an object and
reloads it. A test that needs the aware value should keep a reference to the instance
it created, and say so.
"""

from __future__ import annotations

import sqlite3

from sqlalchemy import Engine, create_engine, event

from healthcurve.db import IdentityBase
from healthcurve.identity.models import Owner, TimezoneStay


def identity_engine() -> Engine:
    """An engine carrying only the owner and timezone-stay tables.

    Deliberately not the whole metadata: it also holds JSONB columns that SQLite cannot
    compile, and nothing here touches them.
    """
    engine = create_engine("sqlite://")

    @event.listens_for(engine, "connect")
    def _attach(dbapi_connection: sqlite3.Connection, _record: object) -> None:
        # Per connection, not once: SQLite has no schemas, and a pooled connection
        # without the ATTACH cannot see the table at all.
        dbapi_connection.execute("ATTACH DATABASE ':memory:' AS identity")

    IdentityBase.metadata.create_all(
        engine,
        tables=[
            IdentityBase.metadata.tables[f"identity.{table}"]
            for table in (Owner.__tablename__, TimezoneStay.__tablename__)
        ],
    )
    return engine
