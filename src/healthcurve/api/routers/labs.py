"""Manual laboratory entry and owner-confirmed CSV import."""

from __future__ import annotations

import hmac
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile, status
from pydantic import Field, model_validator

from healthcurve.api.deps import CurrentOwner, DbSession, require_csrf
from healthcurve.api.routers.doses import resolve_time
from healthcurve.api.schemas import ApiModel, EventTimeIn
from healthcurve.events.base import ConfirmationState, SourceType
from healthcurve.labs.imports import MAX_CSV_BYTES, LabImportError, parse_csv_import
from healthcurve.labs.service import (
    LabConfirmationError,
    confirm_csv,
    create_panel,
    manual_candidate,
)

router = APIRouter(prefix="/labs", tags=["labs"])


class ManualLabResultIn(ApiModel):
    analyte_name: str = Field(min_length=1)
    original_value: str | None = None
    qualitative_result: str | None = None
    original_unit: str | None = None
    original_reference_range: str | None = None
    abnormal_flag: str | None = None
    normalized_analyte_code: str | None = None

    @model_validator(mode="after")
    def has_result(self) -> ManualLabResultIn:
        if self.original_value is None and self.qualitative_result is None:
            raise ValueError("original_value or qualitative_result is required")
        return self


class ManualLabPanelIn(ApiModel):
    specimen_time: EventTimeIn
    report_time: EventTimeIn
    laboratory_name: str | None = Field(default=None, max_length=300)
    accession_id: str | None = Field(default=None, max_length=255)
    specimen_type: str | None = Field(default=None, max_length=255)
    report_status: str | None = Field(default=None, max_length=120)
    results: list[ManualLabResultIn] = Field(min_length=1, max_length=1_000)


@router.post(
    "/manual",
    status_code=status.HTTP_201_CREATED,
    dependencies=[Depends(require_csrf)],
)
def create_manual_lab(payload: ManualLabPanelIn, session: DbSession, owner: CurrentOwner):
    specimen = resolve_time(payload.specimen_time)
    report = resolve_time(payload.report_time)
    if report.occurred_at < specimen.occurred_at:
        raise HTTPException(status_code=422, detail={"code": "report_before_specimen"})
    candidates = [manual_candidate(**result.model_dump()) for result in payload.results]
    try:
        panel = create_panel(
            session,
            owner_id=owner.id,
            specimen_time=specimen,
            report_time=report,
            candidates=candidates,
            source_type=SourceType.WEB,
            confirmation_state=ConfirmationState.DIRECT,
            laboratory_name=payload.laboratory_name,
            accession_id=payload.accession_id,
            specimen_type=payload.specimen_type,
            report_status=payload.report_status,
        )
    except LabConfirmationError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    return _panel_payload(panel, created=True)


async def _parse_csv(
    file: UploadFile,
    *,
    mapping_json: str,
    analyte_map_json: str,
    specimen_local: datetime,
    report_local: datetime,
    timezone: str,
):
    payload = await file.read(MAX_CSV_BYTES + 1)
    try:
        return parse_csv_import(
            source_name=file.filename,
            payload=payload,
            mapping_json=mapping_json,
            analyte_map_json=analyte_map_json,
            specimen_local=specimen_local,
            report_local=report_local,
            timezone=timezone,
        )
    except LabImportError as exc:
        code = str(exc)
        http_status = 413 if code == "csv_size_invalid" else 422
        raise HTTPException(status_code=http_status, detail={"code": code}) from exc
    finally:
        await file.close()


@router.post("/imports/csv/preview", dependencies=[Depends(require_csrf)])
async def preview_csv(
    owner: CurrentOwner,
    file: Annotated[UploadFile, File()],
    mapping_json: Annotated[str, Form()],
    specimen_local: Annotated[datetime, Form()],
    report_local: Annotated[datetime, Form()],
    timezone: Annotated[str | None, Form()] = None,
    analyte_map_json: Annotated[str, Form()] = "{}",
) -> dict[str, Any]:
    parsed = await _parse_csv(
        file,
        mapping_json=mapping_json,
        analyte_map_json=analyte_map_json,
        specimen_local=specimen_local,
        report_local=report_local,
        timezone=timezone or owner.default_timezone,
    )
    return {
        "creates_facts": False,
        "source_sha256": parsed.source_sha256,
        "mapping_sha256": parsed.mapping_sha256,
        "specimen_time": parsed.specimen_time,
        "report_time": parsed.report_time,
        "candidates": [candidate.model_dump() for candidate in parsed.candidates],
    }


@router.post("/imports/csv/confirm", dependencies=[Depends(require_csrf)])
async def confirm_csv_route(
    session: DbSession,
    owner: CurrentOwner,
    file: Annotated[UploadFile, File()],
    expected_sha256: Annotated[str, Form(min_length=64, max_length=64)],
    mapping_json: Annotated[str, Form()],
    specimen_local: Annotated[datetime, Form()],
    report_local: Annotated[datetime, Form()],
    timezone: Annotated[str | None, Form()] = None,
    analyte_map_json: Annotated[str, Form()] = "{}",
) -> dict[str, Any]:
    parsed = await _parse_csv(
        file,
        mapping_json=mapping_json,
        analyte_map_json=analyte_map_json,
        specimen_local=specimen_local,
        report_local=report_local,
        timezone=timezone or owner.default_timezone,
    )
    if not hmac.compare_digest(parsed.source_sha256, expected_sha256.casefold()):
        raise HTTPException(status_code=409, detail={"code": "preview_checksum_mismatch"})
    try:
        result = confirm_csv(session, owner_id=owner.id, parsed=parsed)
    except LabConfirmationError as exc:
        raise HTTPException(status_code=422, detail={"code": str(exc)}) from exc
    return _panel_payload(result.panel, created=result.created)


def _panel_payload(panel: Any, *, created: bool) -> dict[str, Any]:
    return {
        "category": "fact",
        "panel_id": str(panel.id),
        "created": created,
        "specimen": panel.event_time,
        "reported_at": panel.reported_at,
        "reported_local_time": panel.reported_local_time,
        "reported_timezone": panel.reported_timezone,
        "reported_utc_offset_minutes": panel.reported_utc_offset_minutes,
        "result_count": len(panel.results),
    }
