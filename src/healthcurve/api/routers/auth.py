"""Login, logout, session management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from healthcurve.api.deps import CurrentOwner, CurrentSession, DbSession, require_csrf
from healthcurve.config import Environment, get_settings
from healthcurve.identity import service as auth
from healthcurve.operations import audit

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=512)


class LoginResponse(BaseModel):
    #: Returned once on login and echoed in the X-CSRF-Token header thereafter.
    csrf_token: str
    email: str
    display_name: str | None
    default_timezone: str


class WhoAmI(BaseModel):
    email: str
    display_name: str | None
    default_timezone: str
    csrf_token: str


def _set_session_cookie(response: Response, token: str) -> None:
    settings = get_settings()
    response.set_cookie(
        key=auth.SESSION_COOKIE_NAME,
        value=token,
        # HttpOnly: JavaScript must not be able to read the session (T1, T7).
        httponly=True,
        # Secure everywhere except local dev over plain HTTP.
        secure=settings.environment is not Environment.DEV,
        samesite="lax",
        max_age=int(auth.SESSION_LIFETIME.total_seconds()),
        path="/",
    )


@router.post("/login", response_model=LoginResponse)
def login(payload: LoginRequest, request: Request, response: Response, session: DbSession):
    try:
        owner = auth.authenticate(session, payload.email, payload.password)
    except auth.AccountLockedError:
        audit.record(
            session,
            actor=f"email:{payload.email}",
            action=audit.AuditAction.LOGIN_FAILED,
            change_summary="account locked",
        )
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail="too many failed attempts; try again later",
        ) from None
    except auth.AuthenticationError:
        audit.record(
            session,
            actor=f"email:{payload.email}",
            action=audit.AuditAction.LOGIN_FAILED,
        )
        # Same message for unknown account and wrong password: no enumeration.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        ) from None

    auth_session, token = auth.create_session(
        session, owner, user_agent=request.headers.get("user-agent")
    )
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.LOGIN_SUCCEEDED,
        target_type="auth_session",
        target_id=auth_session.id,
    )
    _set_session_cookie(response, token)
    return LoginResponse(
        csrf_token=auth_session.csrf_token,
        email=owner.email,
        display_name=owner.display_name,
        default_timezone=owner.default_timezone,
    )


@router.post(
    "/logout", dependencies=[Depends(require_csrf)], status_code=status.HTTP_204_NO_CONTENT
)
def logout(response: Response, session: DbSession, auth_session: CurrentSession) -> None:
    auth.revoke_session(auth_session)
    audit.record(
        session,
        actor=audit.actor_for_owner(auth_session.owner_id),
        action=audit.AuditAction.LOGOUT,
        target_type="auth_session",
        target_id=auth_session.id,
    )
    response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")


@router.post(
    "/logout-everywhere",
    dependencies=[Depends(require_csrf)],
    status_code=status.HTTP_204_NO_CONTENT,
)
def logout_everywhere(response: Response, session: DbSession, owner: CurrentOwner) -> None:
    """Revoke every session. The control for a lost device (T7)."""
    count = auth.revoke_all_sessions(session, owner.id)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.SESSION_REVOKED,
        change_summary=f"revoked {count} session(s)",
    )
    response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")


@router.get("/me", response_model=WhoAmI)
def me(owner: CurrentOwner, auth_session: CurrentSession) -> WhoAmI:
    return WhoAmI(
        email=owner.email,
        display_name=owner.display_name,
        default_timezone=owner.default_timezone,
        csrf_token=auth_session.csrf_token,
    )


class PasswordChange(BaseModel):
    current_password: str = Field(min_length=1, max_length=512)
    new_password: str = Field(min_length=12, max_length=512)


@router.post(
    "/change-password",
    dependencies=[Depends(require_csrf)],
    status_code=status.HTTP_204_NO_CONTENT,
)
def change_password(
    payload: PasswordChange,
    session: DbSession,
    owner: CurrentOwner,
    auth_session: CurrentSession,
) -> None:
    if not auth.verify_password(owner.password_hash, payload.current_password):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="current password is incorrect"
        )

    owner.password_hash = auth.hash_password(payload.new_password)
    # A password change signs out everything else: if the change was prompted by a
    # suspected compromise, leaving other sessions alive would defeat the point.
    auth.revoke_all_sessions(session, owner.id)
    audit.record(
        session,
        actor=audit.actor_for_owner(owner.id),
        action=audit.AuditAction.PASSWORD_CHANGED,
    )


AuthRouter = Annotated[APIRouter, router]
