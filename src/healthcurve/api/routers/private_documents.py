"""Authenticated access to owner-provided reference documents.

Reference documents live in the private uploads volume, never in the source tree.
Their contents are not interpreted or promoted to physician-approved plan data.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from healthcurve.api.deps import AppSettings, CurrentOwner

router = APIRouter(prefix="/private-documents", tags=["private documents"])

_SICK_DAY_PLAN_RELATIVE_PATH = ("reference", "sick-day-plan.pdf")


@router.get("/sick-day-plan", response_class=FileResponse)
def view_sick_day_plan(
    owner: CurrentOwner,
    settings: AppSettings,
) -> FileResponse:
    """Open the owner's private sick-day reference PDF in the browser."""
    del owner  # Requiring CurrentOwner is the access-control boundary for this file.
    path = settings.uploads_dir.joinpath(*_SICK_DAY_PLAN_RELATIVE_PATH)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "sick_day_plan_not_available"},
        )
    return FileResponse(
        path,
        media_type="application/pdf",
        filename="sick-day-plan.pdf",
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": "sandbox",
            "X-Content-Type-Options": "nosniff",
        },
    )
