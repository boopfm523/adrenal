"""Re-authenticated privacy and deletion controls."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field

from healthcurve import privacy
from healthcurve.api.deps import AppSettings, CurrentOwner, DbSession, require_csrf
from healthcurve.api.routers.exports import create_export
from healthcurve.identity import service as auth
from healthcurve.integrations.garmin.connect_jobs import enqueue_disconnect

router = APIRouter(prefix="/privacy", tags=["privacy"])


class ReauthenticatedRequest(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class IntegrationDeletionRequest(ReauthenticatedRequest):
    delete_data: bool = True
    confirmation: str | None = Field(default=None, max_length=80)


class AccountDeletionRequest(ReauthenticatedRequest):
    confirmation: str


class IntegrationDeletionResponse(BaseModel):
    credentials_deleted: int
    data_rows_deleted: int
    disconnect_requested: bool = False


class PrivateExportRequest(ReauthenticatedRequest):
    include_ai: bool = False
    include_sensitive: bool = True


def _reauthenticate(owner: CurrentOwner, password: str) -> None:
    if not auth.verify_password(owner.password_hash, password):
        raise HTTPException(status_code=403, detail="password is incorrect")


@router.post("/export", dependencies=[Depends(require_csrf)])
def private_export(payload: PrivateExportRequest, session: DbSession, owner: CurrentOwner):
    _reauthenticate(owner, payload.password)
    return create_export(
        session=session,
        owner=owner,
        include_ai=payload.include_ai,
        include_sensitive=payload.include_sensitive,
    )


@router.delete(
    "/records/{record_type}/{record_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_record(
    record_type: str,
    record_id: uuid.UUID,
    payload: ReauthenticatedRequest,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> None:
    _reauthenticate(owner, payload.password)
    try:
        deleted = privacy.delete_record(
            session,
            owner_id=owner.id,
            record_type=record_type,
            record_id=record_id,
            uploads_dir=settings.uploads_dir,
        )
    except privacy.CorrectionHistoryError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except privacy.DeletionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if not deleted:
        raise HTTPException(status_code=404, detail="record not found")


@router.delete(
    "/integrations/{provider}",
    response_model=IntegrationDeletionResponse,
    dependencies=[Depends(require_csrf)],
)
def delete_integration(
    provider: str,
    payload: IntegrationDeletionRequest,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> IntegrationDeletionResponse:
    _reauthenticate(owner, payload.password)
    if provider == "garmin":
        expected = (
            "DISCONNECT GARMIN AND DELETE DATA" if payload.delete_data else "DISCONNECT GARMIN"
        )
        if payload.confirmation != expected:
            raise HTTPException(status_code=422, detail="confirmation phrase does not match")
    try:
        result = privacy.delete_integration(
            session,
            owner_id=owner.id,
            provider=provider,
            delete_data=payload.delete_data,
            telegram_chat_id=settings.telegram_allowed_chat_id,
        )
    except privacy.DeletionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    if result.disconnect_requested:
        enqueue_disconnect(
            session,
            owner_id=owner.id,
            idempotency_key=f"disconnect:{owner.id}:{uuid.uuid4()}",
        )
    return IntegrationDeletionResponse(
        credentials_deleted=result.credentials,
        data_rows_deleted=result.data_rows,
        disconnect_requested=result.disconnect_requested,
    )


@router.delete(
    "/account",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_csrf)],
)
def delete_account(
    payload: AccountDeletionRequest,
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> None:
    _reauthenticate(owner, payload.password)
    if payload.confirmation != "DELETE MY HEALTHCURVE ACCOUNT":
        raise HTTPException(status_code=422, detail="confirmation phrase does not match")
    try:
        privacy.delete_account(
            session,
            owner=owner,
            uploads_dir=settings.uploads_dir,
            telegram_chat_id=settings.telegram_allowed_chat_id,
            report_artifacts_dir=settings.report_artifacts_dir,
        )
    except privacy.DeletionError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")
