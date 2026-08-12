"""Worker handlers and expiration scheduler for complete private exports."""

from __future__ import annotations

import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from healthcurve.labs.documents import DocumentLayout
from healthcurve.operations.jobs import JobQueueError, enqueue
from healthcurve.private_exports import generator, storage
from healthcurve.private_exports.models import PrivateExport
from healthcurve.private_exports.service import (
    PRIVATE_EXPORT_CLEANUP_TASK,
)


def make_generation_handler(
    factory: sessionmaker[Session], *, root: Path, uploads: DocumentLayout
) -> Callable[[Session, Mapping[str, Any]], None]:
    def handle(_session: Session, payload: Mapping[str, object]) -> None:
        try:
            export_id = uuid.UUID(str(payload["export_id"]))
        except (KeyError, TypeError, ValueError) as exc:
            raise JobQueueError("export_payload_invalid") from exc
        generator.generate(factory, root=root, uploads=uploads, export_id=export_id)

    return handle


def make_cleanup_handler(root: Path) -> Callable[[Session, Mapping[str, Any]], None]:
    def handle(session: Session, _payload: Mapping[str, Any]) -> None:
        now = datetime.now(UTC)
        rows = session.scalars(
            select(PrivateExport)
            .where(
                PrivateExport.expires_at <= now,
                PrivateExport.purged_at.is_(None),
            )
            .order_by(PrivateExport.expires_at, PrivateExport.id)
            .with_for_update(skip_locked=True)
            .limit(100)
        )
        for export in rows:
            if export.relative_path:
                storage.delete(root, export.relative_path)
            export.purged_at = now
        storage.remove_stale_temporary_files(
            root, older_than_epoch=(now - timedelta(days=1)).timestamp()
        )

    return handle


def schedule_cleanup(session: Session, now: datetime) -> object:
    local = now.astimezone(UTC)
    return enqueue(
        session,
        task=PRIVATE_EXPORT_CLEANUP_TASK,
        payload={},
        idempotency_key=f"private-export-cleanup:{local.date().isoformat()}",
        run_at=local,
        max_attempts=5,
    )
