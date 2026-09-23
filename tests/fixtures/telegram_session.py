"""A session double for Telegram handler tests that never reach a database.

The handlers resolve the owner's current zone from the stay ledger on every capture
path, so a bare ``MagicMock(spec=Session)`` now answers that lookup with a mock whose
``.timezone`` is not a zone name. Configuring the lookup to find nothing is not a
workaround: it is the state these tests describe -- an owner who has not travelled,
whose entries resolve against the home zone exactly as they always did.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from sqlalchemy.orm import Session


def telegram_session() -> MagicMock:
    """A mocked session whose timezone-stay lookup returns no rows.

    Returns the mock itself, not a ``Session``, so callers keep the handle they
    need to configure further queries and assert on calls.
    """
    session = MagicMock(spec=Session)
    session.scalar.return_value = None
    return session
