"""Owner-reviewed import of Garmin export files.

Preview is deliberately database-free. Confirmation reparses the exact upload and
checks its digest before any recorded fact is created (SAFE-11, SAFE-14).
"""

from __future__ import annotations

import hmac
from decimal import Decimal
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.integrations.garmin.parser import (
    MAX_UPLOAD_BYTES,
    SUPPORTED_METRICS,
    ActivityCandidate,
    GarminImportError,
    MetricCandidate,
    ParsedGarminImport,
    SleepCandidate,
    parse_upload,
)
from healthcurve.integrations.garmin.service import confirm_import

router = APIRouter(prefix="/integrations/garmin/imports", tags=["garmin"])


async def _parse(file: UploadFile, timezone: str) -> ParsedGarminImport:
    payload = await file.read(MAX_UPLOAD_BYTES + 1)
    try:
        return parse_upload(file.filename, payload, timezone)
    except GarminImportError as exc:
        code = str(exc)
        http_status = (
            status.HTTP_413_CONTENT_TOO_LARGE
            if code in {"file_too_large", "archive_expanded_too_large"}
            else status.HTTP_422_UNPROCESSABLE_CONTENT
        )
        raise HTTPException(status_code=http_status, detail={"code": code}) from exc
    finally:
        await file.close()


@router.post("/preview", dependencies=[Depends(require_csrf)])
async def preview_import(
    owner: CurrentOwner,
    file: Annotated[UploadFile, File()],
    timezone: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Parse locally and return candidates; this endpoint creates no facts."""
    parsed = await _parse(file, timezone or owner.default_timezone)
    return _preview_payload(parsed)


@router.post("/confirm", dependencies=[Depends(require_csrf)])
async def confirm_import_route(
    session: DbSession,
    owner: CurrentOwner,
    file: Annotated[UploadFile, File()],
    expected_sha256: Annotated[str, Form(min_length=64, max_length=64)],
    timezone: Annotated[str | None, Form()] = None,
) -> dict[str, Any]:
    """Reparse and confirm an unchanged preview as immutable recorded facts."""
    parsed = await _parse(file, timezone or owner.default_timezone)
    if not hmac.compare_digest(parsed.source_sha256, expected_sha256.casefold()):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail={"code": "preview_checksum_mismatch"},
        )
    result = confirm_import(session, owner_id=owner.id, parsed=parsed)
    return {
        "batch_id": str(result.batch.id),
        "source_sha256": result.batch.source_sha256,
        "created": result.created,
        "metric_count": result.metric_count,
        "sleep_count": result.sleep_count,
        "activity_count": result.activity_count,
    }


def _preview_payload(parsed: ParsedGarminImport) -> dict[str, Any]:
    return {
        "creates_facts": False,
        "source_name": parsed.source_name,
        "source_sha256": parsed.source_sha256,
        "source_members": parsed.source_members,
        "sdk_profile_version": parsed.sdk_profile_version,
        "supported_metrics": sorted(SUPPORTED_METRICS),
        "observed_metrics": parsed.observed_metrics,
        "missing_metrics": parsed.missing_metrics,
        "device_attributions": parsed.device_attributions,
        "warnings": parsed.warnings,
        "candidates": [_candidate_payload(candidate) for candidate in parsed.candidates],
    }


def _candidate_payload(candidate: MetricCandidate | SleepCandidate | ActivityCandidate):
    assert candidate.time is not None and candidate.source is not None
    common: dict[str, Any] = {
        "kind": candidate.kind,
        "occurred_at": candidate.time.occurred_at,
        "local_time": candidate.time.local_time,
        "timezone": candidate.time.timezone,
        "source_member": candidate.source.member_name,
        "source_sha256": candidate.source.member_sha256,
        "device": candidate.source.device.as_dict(),
    }
    if isinstance(candidate, MetricCandidate):
        common.update(
            metric_type=candidate.metric_type.value,
            value=_decimal(candidate.value),
            unit=candidate.unit,
            field_name=candidate.field_name,
            period_end_at=candidate.period_end_at,
        )
    elif isinstance(candidate, SleepCandidate):
        common.update(
            ended_at=candidate.ended_at,
            overall_sleep_score=candidate.overall_sleep_score,
            stage_count=candidate.stage_count,
        )
    else:
        common.update(
            ended_at=candidate.ended_at,
            sport=candidate.sport,
            sub_sport=candidate.sub_sport,
            title=candidate.title,
            elapsed_seconds=_decimal(candidate.elapsed_seconds),
            distance_m=_decimal(candidate.distance_m),
            calories=candidate.calories,
            average_heart_rate=candidate.average_heart_rate,
            maximum_heart_rate=candidate.maximum_heart_rate,
        )
    return common


def _decimal(value: Decimal | None) -> str | None:
    return None if value is None else str(value)
