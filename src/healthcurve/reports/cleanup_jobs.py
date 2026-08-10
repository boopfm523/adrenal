"""Durable cleanup of immutable report files removed by privacy deletion."""

from __future__ import annotations

import uuid
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.orm import Session

from healthcurve.operations.jobs import Job, JobQueueError, enqueue
from healthcurve.operations.worker import JobHandler
from healthcurve.reports.storage import ArtifactStorageError, delete_snapshot_artifacts

REPORT_ARTIFACT_CLEANUP_TASK = "reports.snapshot_artifacts.cleanup"


def enqueue_snapshot_artifact_cleanup(
    session: Session, *, owner_id: uuid.UUID, snapshot_id: uuid.UUID
) -> Job:
    """Queue opaque ownership and snapshot IDs with stable retry identity."""
    return enqueue(
        session,
        task=REPORT_ARTIFACT_CLEANUP_TASK,
        payload={"owner_id": str(owner_id), "snapshot_id": str(snapshot_id)},
        idempotency_key=f"report-snapshot:{owner_id}:{snapshot_id}",
        priority=100,
        max_attempts=20,
    )


def make_snapshot_artifact_cleanup_handler(root: Path) -> JobHandler:
    def handle(_session: Session, payload: Mapping[str, object]) -> None:
        if set(payload) != {"owner_id", "snapshot_id"}:
            raise JobQueueError("report_cleanup_payload_invalid")
        try:
            owner_id = uuid.UUID(str(payload["owner_id"]))
            snapshot_id = uuid.UUID(str(payload["snapshot_id"]))
        except (ValueError, TypeError, AttributeError) as exc:
            raise JobQueueError("report_cleanup_payload_invalid") from exc
        try:
            delete_snapshot_artifacts(root, owner_id=owner_id, snapshot_id=snapshot_id)
        except (ArtifactStorageError, OSError) as exc:
            raise JobQueueError("report_artifact_cleanup_failed") from exc

    return handle
