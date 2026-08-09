"""Canonical creation and verification of immutable report snapshots."""

from __future__ import annotations

import copy
import hashlib
import json
import uuid
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any, Final

from sqlalchemy.orm import Session

from healthcurve.reports.models import ReportSnapshot

PARTITIONS: Final[tuple[str, ...]] = ("fact", "plan", "patient_note", "ai")
DEFAULT_RENDER_VERSION: Final = "report-v1"


class SnapshotValidationError(ValueError):
    """The requested snapshot would violate a report safety boundary."""


def _json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): _json_value(item) for key, item in sorted(value.items())}
    if isinstance(value, (list, tuple)):
        return [_json_value(item) for item in value]
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, (Decimal, uuid.UUID)):
        return str(value)
    if isinstance(value, Enum):
        return _json_value(value.value)
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    raise SnapshotValidationError(f"unsupported canonical value type: {type(value).__name__}")


def _validate_partitions(value: dict[str, Any], *, field: str) -> None:
    if set(value) != set(PARTITIONS):
        raise SnapshotValidationError(f"{field} must contain exactly: {', '.join(PARTITIONS)}")


def _validate_manifest(manifest: dict[str, Any]) -> None:
    _validate_partitions(manifest, field="source manifest")
    for partition, record_ids in manifest.items():
        if not isinstance(record_ids, list) or not all(
            isinstance(record_id, str) and record_id for record_id in record_ids
        ):
            raise SnapshotValidationError(
                f"source manifest partition {partition} must contain non-empty string IDs"
            )


def _validate_content(content: dict[str, Any]) -> None:
    _validate_partitions(content, field="snapshot content")
    for partition, records in content.items():
        if not isinstance(records, list) or not all(isinstance(record, dict) for record in records):
            raise SnapshotValidationError(
                f"snapshot content partition {partition} must contain record objects"
            )


def _validate_metrics(metrics: dict[str, Any]) -> None:
    for name, metric in metrics.items():
        if not isinstance(metric, dict):
            raise SnapshotValidationError(f"metric {name} must be an object")
        if not isinstance(metric.get("definition"), str) or not metric["definition"]:
            raise SnapshotValidationError(f"metric {name} requires a definition")
        if not isinstance(metric.get("timezone"), str) or not metric["timezone"]:
            raise SnapshotValidationError(f"metric {name} requires a timezone")


def canonical_payload(
    *,
    date_from: date,
    date_to: date,
    timezone: str,
    selected_sections: list[str],
    include_ai: bool,
    source_manifest: dict[str, list[str]],
    metric_values: dict[str, object],
    snapshot_content: dict[str, object],
    render_version: str,
) -> dict[str, Any]:
    """Validate and normalize every byte that defines a reproducible report."""
    if date_to < date_from:
        raise SnapshotValidationError("report end date cannot precede start date")
    if (
        not timezone
        or not selected_sections
        or not all(selected_sections)
        or len(set(selected_sections)) != len(selected_sections)
        or not render_version
    ):
        raise SnapshotValidationError(
            "timezone, unique selected sections, and render version are required"
        )
    _validate_manifest(source_manifest)
    _validate_content(snapshot_content)
    _validate_metrics(metric_values)
    if not include_ai and (source_manifest["ai"] or snapshot_content["ai"]):
        raise SnapshotValidationError("AI content requires explicit opt-in")
    return _json_value(
        {
            "date_from": date_from,
            "date_to": date_to,
            "timezone": timezone,
            "selected_sections": selected_sections,
            "include_ai": include_ai,
            "source_manifest": source_manifest,
            "metric_values": metric_values,
            "snapshot_content": snapshot_content,
            "render_version": render_version,
        }
    )


def checksum(payload: dict[str, Any]) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def create_snapshot(
    session: Session,
    *,
    owner_id: uuid.UUID,
    date_from: date,
    date_to: date,
    timezone: str,
    selected_sections: list[str],
    source_manifest: dict[str, list[str]],
    metric_values: dict[str, object],
    snapshot_content: dict[str, object],
    include_ai: bool = False,
    render_version: str = DEFAULT_RENDER_VERSION,
) -> ReportSnapshot:
    payload = canonical_payload(
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        selected_sections=selected_sections,
        include_ai=include_ai,
        source_manifest=source_manifest,
        metric_values=metric_values,
        snapshot_content=snapshot_content,
        render_version=render_version,
    )
    row = ReportSnapshot(
        owner_id=owner_id,
        date_from=date_from,
        date_to=date_to,
        timezone=timezone,
        selected_sections=payload["selected_sections"],
        include_ai=include_ai,
        source_manifest=payload["source_manifest"],
        metric_values=payload["metric_values"],
        snapshot_content=payload["snapshot_content"],
        render_version=render_version,
        canonical_sha256=checksum(payload),
    )
    session.add(row)
    return row


def document(snapshot: ReportSnapshot) -> dict[str, Any]:
    """Return only frozen data after proving that it still matches its checksum."""
    payload = canonical_payload(
        date_from=snapshot.date_from,
        date_to=snapshot.date_to,
        timezone=snapshot.timezone,
        selected_sections=snapshot.selected_sections,
        include_ai=snapshot.include_ai,
        source_manifest=snapshot.source_manifest,
        metric_values=snapshot.metric_values,
        snapshot_content=snapshot.snapshot_content,
        render_version=snapshot.render_version,
    )
    if checksum(payload) != snapshot.canonical_sha256:
        raise SnapshotValidationError("stored report snapshot checksum does not match")
    return copy.deepcopy(payload)
