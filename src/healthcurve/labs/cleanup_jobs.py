"""Durable, idempotent cleanup of deleted lab-document storage."""

from __future__ import annotations

import uuid
from collections.abc import Mapping

from sqlalchemy.orm import Session

from healthcurve.labs.documents import DocumentLayout, mark_deleted
from healthcurve.operations.jobs import Job, JobQueueError, enqueue
from healthcurve.operations.worker import JobHandler

LAB_DOCUMENT_CLEANUP_TASK = "labs.document.cleanup"


def enqueue_document_cleanup(session: Session, document_id: uuid.UUID) -> Job:
    """Queue only an opaque document ID in the caller's deletion transaction."""
    return enqueue(
        session,
        task=LAB_DOCUMENT_CLEANUP_TASK,
        payload={"document_id": str(document_id)},
        idempotency_key=f"lab-document:{document_id}",
        priority=100,
        max_attempts=20,
    )


def make_document_cleanup_handler(layout: DocumentLayout) -> JobHandler:
    """Write the tombstone first, then remove every private derivative."""

    def handle(_session: Session, payload: Mapping[str, object]) -> None:
        if set(payload) != {"document_id"}:
            raise JobQueueError("lab_cleanup_payload_invalid")
        try:
            document_id = uuid.UUID(str(payload["document_id"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise JobQueueError("lab_cleanup_payload_invalid") from exc
        try:
            mark_deleted(layout, document_id)
        except OSError as exc:
            raise JobQueueError("lab_document_cleanup_failed") from exc

    return handle
