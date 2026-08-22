"""Authenticated access to owner-provided reference documents.

Reference documents live in the private uploads volume, never in the source tree.
Their contents are not interpreted or promoted to physician-approved plan data.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from healthcurve.api.deps import AppSettings, CurrentOwner

router = APIRouter(prefix="/private-documents", tags=["private documents"])

_SICK_DAY_RULES_RELATIVE_PATH = ("reference", "sick-day-rules.html")


@router.get("/sick-day-plan", response_class=FileResponse)
def view_sick_day_rules(
    owner: CurrentOwner,
    settings: AppSettings,
) -> FileResponse:
    """Open the owner's private sick-day rules page in the browser."""
    del owner  # Requiring CurrentOwner is the access-control boundary for this file.
    path = settings.uploads_dir.joinpath(*_SICK_DAY_RULES_RELATIVE_PATH)
    if not path.is_file():
        raise HTTPException(
            status_code=404,
            detail={"code": "sick_day_rules_not_available"},
        )
    return FileResponse(
        path,
        media_type="text/html; charset=utf-8",
        filename="sick-day-rules.html",
        content_disposition_type="inline",
        headers={
            "Cache-Control": "private, no-store",
            "Content-Security-Policy": ("sandbox; default-src 'none'; style-src 'unsafe-inline'"),
            "X-Content-Type-Options": "nosniff",
        },
    )
