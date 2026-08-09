"""Login, logout, session management."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from pydantic import BaseModel, EmailStr, Field

from healthcurve import mfa
from healthcurve.api.deps import (
    AppRateLimiter,
    AppSettings,
    CurrentOwner,
    CurrentSession,
    DbSession,
    enforce_rate_limit,
    require_csrf,
)
from healthcurve.config import Environment, get_settings
from healthcurve.identity import service as auth
from healthcurve.integrations.credentials import CredentialError
from healthcurve.operations import audit
from healthcurve.operations.rate_limit import RateLimitPolicy
from healthcurve.operations.telemetry import OperationalEvent

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=1, max_length=512)
    second_factor_code: str | None = Field(default=None, min_length=6, max_length=64)


class LoginResponse(BaseModel):
    #: Returned once on login and echoed in the X-CSRF-Token header thereafter.
    csrf_token: str
    email: str
    display_name: str | None
    default_timezone: str
    mfa_enabled: bool


class WhoAmI(BaseModel):
    email: str
    display_name: str | None
    default_timezone: str
    csrf_token: str
    mfa_enabled: bool


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
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    session: DbSession,
    settings: AppSettings,
    limiter: AppRateLimiter,
):
    # Check before password hashing, which is intentionally expensive. The normalized
    # address is hashed by the limiter, so Redis never receives an email address.
    enforce_rate_limit(
        response,
        limiter,
        scope="login",
        identity=str(payload.email),
        policy=RateLimitPolicy(settings.login_rate_limit, settings.login_rate_window_s),
    )
    try:
        owner = auth.authenticate(session, payload.email, payload.password)
    except auth.AccountLockedError:
        request.app.state.telemetry.record(OperationalEvent.AUTH_FAILURE)
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
        request.app.state.telemetry.record(OperationalEvent.AUTH_FAILURE)
        audit.record(
            session,
            actor=f"email:{payload.email}",
            action=audit.AuditAction.LOGIN_FAILED,
        )
        # Same message for unknown account and wrong password: no enumeration.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="invalid credentials"
        ) from None

    if owner.mfa_enabled:
        if payload.second_factor_code is None:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="second factor required",
            )
        try:
            mfa.verify_second_factor(session, owner, settings, payload.second_factor_code)
        except mfa.InvalidSecondFactor:
            request.app.state.telemetry.record(OperationalEvent.AUTH_FAILURE)
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail="invalid second factor",
            ) from None
        except (mfa.MfaConfigurationError, CredentialError):
            raise HTTPException(
                status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
                detail="second factor verification is unavailable",
            ) from None
    elif settings.mfa_required:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="MFA enrollment is required; use the local mfa-enroll command",
        )

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
        mfa_enabled=owner.mfa_enabled,
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
        mfa_enabled=owner.mfa_enabled,
    )


class MfaStatus(BaseModel):
    enabled: bool
    recovery_codes_remaining: int


class PasswordProof(BaseModel):
    password: str = Field(min_length=1, max_length=512)


class MfaEnrollmentOut(BaseModel):
    secret: str
    provisioning_uri: str


class MfaCode(BaseModel):
    code: str = Field(min_length=6, max_length=64)


class MfaRecoveryCodesOut(BaseModel):
    recovery_codes: list[str]


class MfaChangeProof(PasswordProof, MfaCode):
    pass


def _require_password(owner: CurrentOwner, password: str) -> None:
    if not auth.verify_password(owner.password_hash, password):
        raise HTTPException(status_code=403, detail="current password is incorrect")


@router.get("/mfa", response_model=MfaStatus)
def mfa_status(session: DbSession, owner: CurrentOwner) -> MfaStatus:
    return MfaStatus(
        enabled=owner.mfa_enabled,
        recovery_codes_remaining=mfa.recovery_codes_remaining(session, owner.id),
    )


@router.post(
    "/mfa/enrollment",
    response_model=MfaEnrollmentOut,
    dependencies=[Depends(require_csrf)],
)
def start_mfa_enrollment(
    payload: PasswordProof,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> MfaEnrollmentOut:
    _require_password(owner, payload.password)
    try:
        enrollment = mfa.start_enrollment(session, owner, settings)
    except mfa.MfaError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except CredentialError as exc:
        raise HTTPException(status_code=503, detail="MFA encryption is unavailable") from exc
    return MfaEnrollmentOut(
        secret=enrollment.secret,
        provisioning_uri=enrollment.provisioning_uri,
    )


@router.post(
    "/mfa/enrollment/confirm",
    response_model=MfaRecoveryCodesOut,
    dependencies=[Depends(require_csrf)],
)
def confirm_mfa_enrollment(
    payload: MfaCode,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> MfaRecoveryCodesOut:
    try:
        codes = mfa.confirm_enrollment(session, owner, settings, payload.code)
    except (mfa.InvalidSecondFactor, mfa.EnrollmentNotStarted) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except (mfa.MfaConfigurationError, CredentialError) as exc:
        raise HTTPException(status_code=503, detail="MFA encryption is unavailable") from exc
    return MfaRecoveryCodesOut(recovery_codes=codes)


@router.post(
    "/mfa/recovery-codes",
    response_model=MfaRecoveryCodesOut,
    dependencies=[Depends(require_csrf)],
)
def regenerate_mfa_recovery_codes(
    payload: MfaChangeProof,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> MfaRecoveryCodesOut:
    _require_password(owner, payload.password)
    try:
        mfa.verify_second_factor(session, owner, settings, payload.code)
    except (mfa.MfaError, CredentialError) as exc:
        raise HTTPException(status_code=403, detail="second factor is incorrect") from exc
    codes = mfa.regenerate_recovery_codes(
        session,
        owner,
        action=audit.AuditAction.MFA_RECOVERY_CODES_REGENERATED,
    )
    return MfaRecoveryCodesOut(recovery_codes=codes)


@router.delete(
    "/mfa",
    dependencies=[Depends(require_csrf)],
    status_code=status.HTTP_204_NO_CONTENT,
)
def remove_mfa(
    payload: MfaChangeProof,
    response: Response,
    session: DbSession,
    owner: CurrentOwner,
    settings: AppSettings,
) -> None:
    _require_password(owner, payload.password)
    try:
        mfa.verify_second_factor(session, owner, settings, payload.code)
    except (mfa.MfaError, CredentialError) as exc:
        raise HTTPException(status_code=403, detail="second factor is incorrect") from exc
    mfa.remove(session, owner)
    auth.revoke_all_sessions(session, owner.id)
    response.delete_cookie(auth.SESSION_COOKIE_NAME, path="/")


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
