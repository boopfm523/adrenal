"""Request, lookup, and lifecycle operations for queued private exports."""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from healthcurve.operations.jobs import Job, enqueue
from healthcurve.private_exports.models import PrivateExport

PRIVATE_EXPORT_TASK = "privacy.complete_export.generate"
PRIVATE_EXPORT_CLEANUP_TASK = "privacy.complete_export.cleanup"
EXPORT_RETENTION = timedelta(days=7)


class PrivateExportError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        self.reason_code = reason_code
        super().__init__(reason_code)


@dataclass(frozen=True, slots=True)
class ExportRequest:
    export: PrivateExport
    job: Job
    replayed: bool


def _fingerprint(*, include_ai: bool, include_sensitive: bool) -> str:
    canonical = json.dumps(
        {"include_ai": include_ai, "include_sensitive": include_sensitive},
        sort_keys=True,
        separators=(",", ":"),
    ).encode()
    return hashlib.sha256(canonical).hexdigest()


def request_export(
    session: Session,
    *,
    owner_id: uuid.UUID,
    idempotency_key: str,
    include_ai: bool,
    include_sensitive: bool,
    now: datetime | None = None,
) -> ExportRequest:
    if not idempotency_key or len(idempotency_key) > 255:
        raise PrivateExportError("export_idempotency_key_invalid")
    created_at = (now or datetime.now(UTC)).astimezone(UTC)
    fingerprint = _fingerprint(include_ai=include_ai, include_sensitive=include_sensitive)
    existing = session.scalar(
        select(PrivateExport).where(
            PrivateExport.owner_id == owner_id,
            PrivateExport.idempotency_key == idempotency_key,
        )
    )
    if existing is not None:
        if existing.request_fingerprint_sha256 != fingerprint:
            raise PrivateExportError("export_idempotency_options_conflict")
        job = session.get(Job, existing.job_id)
        if job is None:  # pragma: no cover - FK integrity
            raise PrivateExportError("export_job_missing")
        return ExportRequest(existing, job, True)

    export_id = uuid.uuid4()
    job = enqueue(
        session,
        task=PRIVATE_EXPORT_TASK,
        payload={"export_id": str(export_id)},
        idempotency_key=(
            f"owner:{owner_id}:export:{hashlib.sha256(idempotency_key.encode()).hexdigest()}"
        ),
        max_attempts=5,
    )
    export = PrivateExport(
        id=export_id,
        owner_id=owner_id,
        job_id=job.id,
        idempotency_key=idempotency_key,
        request_fingerprint_sha256=fingerprint,
        include_ai=include_ai,
        include_sensitive=include_sensitive,
        expires_at=created_at + EXPORT_RETENTION,
    )
    session.add(export)
    try:
        session.flush()
    except IntegrityError as exc:
        raise PrivateExportError("export_enqueue_conflict") from exc
    return ExportRequest(export, job, False)


def owned_export(
    session: Session, *, owner_id: uuid.UUID, export_id: uuid.UUID
) -> tuple[PrivateExport, Job] | None:
    row = session.execute(
        select(PrivateExport, Job)
        .join(Job, Job.id == PrivateExport.job_id)
        .where(PrivateExport.id == export_id, PrivateExport.owner_id == owner_id)
    ).one_or_none()
    return None if row is None else (row[0], row[1])
