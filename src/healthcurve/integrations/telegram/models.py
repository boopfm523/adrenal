"""Telegram update deduplication.

Telegram retries an update until it receives a 200. Without a record of what has been
seen, a retry would create a second draft from one message (threat model T4, replay).

Only the update ID and outcome are stored -- never the message text, which lives on
the draft and is purged when the draft resolves.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, String, func
from sqlalchemy.orm import Mapped, mapped_column

from healthcurve.db import OPS_SCHEMA, OpsBase


class TelegramUpdate(OpsBase):
    __tablename__ = "telegram_update"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)

    #: Telegram's own monotonic update id. Unique, so a replay is a no-op.
    update_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False, index=True)

    received_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, server_default=func.now()
    )
    #: processed | rejected_chat | rejected_secret | error | ignored
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    draft_id: Mapped[uuid.UUID | None] = mapped_column()

    __table_args__ = (OPS_SCHEMA,)
