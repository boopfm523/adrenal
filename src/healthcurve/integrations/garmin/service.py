"""Persist an owner-confirmed Garmin preview as idempotent recorded facts."""

from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import func, select, text
from sqlalchemy.orm import Session

from healthcurve.events.base import ConfirmationState, EventMixin, SourceType
from healthcurve.integrations.garmin.models import (
    GarminActivityEvent,
    GarminImportBatch,
    GarminMetricEvent,
    GarminSleepEvent,
    GarminSleepStage,
    GarminSleepStageInterval,
    GarminSourceMixin,
)
from healthcurve.integrations.garmin.parser import (
    ActivityCandidate,
    GarminCandidate,
    MetricCandidate,
    ParsedGarminImport,
    SleepCandidate,
)
from healthcurve.operations import audit


@dataclass(frozen=True)
class ConfirmResult:
    batch: GarminImportBatch
    created: bool
    metric_count: int
    sleep_count: int
    activity_count: int


def confirm_import(
    session: Session, *, owner_id: uuid.UUID, parsed: ParsedGarminImport
) -> ConfirmResult:
    """Create one batch and all its facts in the caller's transaction."""
    # Serialize confirmations of the same owner/file in PostgreSQL so two browser
    # retries cannot race between the lookup and unique insert.
    if session.get_bind().dialect.name == "postgresql":
        lock_material = f"{owner_id}:{parsed.source_sha256}".encode()
        lock_key = int.from_bytes(hashlib.sha256(lock_material).digest()[:8], "big", signed=True)
        session.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": lock_key})
    existing = session.scalar(
        select(GarminImportBatch).where(
            GarminImportBatch.owner_id == owner_id,
            GarminImportBatch.source_sha256 == parsed.source_sha256,
        )
    )
    if existing is not None:
        return _result_for_existing(session, existing)

    batch = GarminImportBatch(
        id=uuid.uuid4(),
        owner_id=owner_id,
        source_name=parsed.source_name,
        source_media_type=parsed.source_media_type,
        source_sha256=parsed.source_sha256,
        source_byte_size=len(parsed.source_payload),
        source_payload=parsed.source_payload,
        source_members=parsed.source_members,
        sdk_profile_version=parsed.sdk_profile_version,
        observed_metrics=parsed.observed_metrics,
        missing_metrics=parsed.missing_metrics,
        device_attributions=parsed.device_attributions,
    )
    session.add(batch)
    # The event models carry the batch UUID but no ORM relationship, so make the
    # parent row visible before SQLAlchemy orders inserts for the child tables.
    session.flush([batch])

    metric_count = 0
    sleep_count = 0
    activity_count = 0
    recorded_at = datetime.now(UTC)
    for index, candidate in enumerate(parsed.candidates):
        common = _event_fields(
            owner_id=owner_id,
            batch_id=batch.id,
            candidate=candidate,
            index=index,
            recorded_at=recorded_at,
        )
        if isinstance(candidate, MetricCandidate):
            assert candidate.time is not None and candidate.source is not None
            row: EventMixin = GarminMetricEvent(
                **common,
                metric_type=candidate.metric_type,
                value=candidate.value,
                unit=candidate.unit,
                period_end_at=candidate.period_end_at,
                aggregation="interval" if candidate.period_end_at is not None else "point",
                sample_interval_seconds=None,
                garmin_field_name=candidate.field_name,
            )
            metric_count += 1
        elif isinstance(candidate, SleepCandidate):
            assert (
                candidate.time is not None
                and candidate.ended_at is not None
                and candidate.source is not None
            )
            row = GarminSleepEvent(
                **common,
                ended_at=candidate.ended_at,
                overall_sleep_score=candidate.overall_sleep_score,
                stage_count=candidate.stage_count,
                duration_seconds=int(
                    (candidate.ended_at - candidate.time.occurred_at).total_seconds()
                ),
                garmin_duration_source="calculated_from_bounds",
                awakenings=None,
            )
            sleep_count += 1
        else:
            assert (
                isinstance(candidate, ActivityCandidate)
                and candidate.time is not None
                and candidate.ended_at is not None
                and candidate.source is not None
            )
            row = GarminActivityEvent(
                **common,
                ended_at=candidate.ended_at,
                sport=candidate.sport,
                sub_sport=candidate.sub_sport,
                title=candidate.title,
                elapsed_seconds=candidate.elapsed_seconds,
                distance_miles=candidate.distance_miles,
                calories=candidate.calories,
                average_heart_rate=candidate.average_heart_rate,
                maximum_heart_rate=candidate.maximum_heart_rate,
                source_notes=candidate.source_notes,
            )
            activity_count += 1
        row.apply_event_time(candidate.time)
        session.add(row)
        if isinstance(candidate, SleepCandidate):
            session.flush([row])
            session.add_all(
                GarminSleepStageInterval(
                    sleep_event_id=row.id,
                    ordinal=ordinal,
                    stage=GarminSleepStage.AWAKE,
                    started_at=interval.started_at,
                    ended_at=interval.ended_at,
                )
                for ordinal, interval in enumerate(candidate.stage_intervals)
                if interval.started_at is not None and interval.ended_at is not None
            )

    audit.record(
        session,
        actor=audit.actor_for_owner(owner_id),
        action=audit.AuditAction.INTEGRATION_IMPORT_CONFIRMED,
        target_type="garmin_import_batch",
        target_id=batch.id,
        change_summary=(f"metrics={metric_count};sleep={sleep_count};activities={activity_count}"),
    )
    session.flush()
    return ConfirmResult(
        batch=batch,
        created=True,
        metric_count=metric_count,
        sleep_count=sleep_count,
        activity_count=activity_count,
    )


def _event_fields(
    *,
    owner_id: uuid.UUID,
    batch_id: uuid.UUID,
    candidate: GarminCandidate,
    index: int,
    recorded_at: datetime,
) -> dict[str, object]:
    assert candidate.time is not None and candidate.source is not None
    source = candidate.source
    identity = (
        f"{source.member_sha256}:{candidate.kind}:{index}:{candidate.time.occurred_at.isoformat()}"
    )
    source_type = (
        SourceType.CSV_IMPORT
        if source.member_name.casefold().endswith(".csv")
        else SourceType.FILE_IMPORT
    )
    return {
        "id": uuid.uuid4(),
        "owner_id": owner_id,
        "occurred_at": candidate.time.occurred_at,
        "local_time": candidate.time.local_time,
        "timezone": candidate.time.timezone,
        "utc_offset_minutes": candidate.time.utc_offset_minutes,
        "recorded_at": recorded_at,
        "source_type": source_type,
        "provider_id": hashlib.sha256(identity.encode()).hexdigest(),
        "source_revision": source.member_sha256,
        "import_batch_id": batch_id,
        "confirmation_state": ConfirmationState.CONFIRMED_FROM_DRAFT,
        "garmin_import_batch_id": batch_id,
        "garmin_sync_run_id": None,
        "garmin_source_member": source.member_name,
        "garmin_manufacturer": source.device.manufacturer,
        "garmin_product_name": source.device.product_name,
        "garmin_device_serial_hash": source.device.serial_hash,
    }


def _result_for_existing(session: Session, batch: GarminImportBatch) -> ConfirmResult:
    def count(model: type[GarminSourceMixin]) -> int:
        return int(
            session.scalar(
                select(func.count())
                .select_from(model)
                .where(model.garmin_import_batch_id == batch.id)
            )
            or 0
        )

    return ConfirmResult(
        batch=batch,
        created=False,
        metric_count=count(GarminMetricEvent),
        sleep_count=count(GarminSleepEvent),
        activity_count=count(GarminActivityEvent),
    )
