"""Shared FastAPI dependencies: database session, current owner, CSRF.

Every route that touches health data depends on :func:`current_owner`. There is no
route that reads a fact without one, which is what makes owner-scoping a property of
the application rather than a habit.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Cookie, Depends, Header, HTTPException, Request, status
from sqlalchemy.orm import Session

from healthcurve.config import Settings
from healthcurve.db import session_scope
from healthcurve.identity import service as auth
from healthcurve.identity.models import AuthSession, Owner

DbSession = Annotated[Session, Depends(session_scope)]

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


def current_session(
    session: DbSession,
    hc_session: Annotated[str | None, Cookie(alias=auth.SESSION_COOKIE_NAME)] = None,
) -> AuthSession:
    resolved = auth.resolve_session(session, hc_session or "")
    if resolved is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="not authenticated",
            headers={"WWW-Authenticate": "Cookie"},
        )
    return resolved


CurrentSession = Annotated[AuthSession, Depends(current_session)]


def current_owner(session: DbSession, auth_session: CurrentSession) -> Owner:
    owner = session.get(Owner, auth_session.owner_id)
    if owner is None:  # pragma: no cover -- FK makes this unreachable
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="not authenticated")
    return owner


CurrentOwner = Annotated[Owner, Depends(current_owner)]


def optional_current_owner(
    session: DbSession,
    hc_session: Annotated[str | None, Cookie(alias=auth.SESSION_COOKIE_NAME)] = None,
) -> Owner | None:
    """Resolve an owner when a valid session exists, without making login mandatory.

    This is deliberately separate from ``CurrentOwner`` and is only suitable for a
    route whose anonymous response contains no owner-scoped data.
    """
    resolved = auth.resolve_session(session, hc_session or "")
    if resolved is None:
        return None
    return session.get(Owner, resolved.owner_id)


OptionalCurrentOwner = Annotated[Owner | None, Depends(optional_current_owner)]


def require_csrf(
    request: Request,
    auth_session: CurrentSession,
    x_csrf_token: Annotated[str | None, Header(alias=auth.CSRF_HEADER_NAME)] = None,
) -> None:
    """Reject a state-changing request without a matching CSRF token (T1).

    Safe methods pass through; a cookie alone must never be enough to cause a write.
    """
    if request.method not in _UNSAFE_METHODS:
        return
    if not auth.verify_csrf(auth_session, x_csrf_token):
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail="missing or invalid CSRF token"
        )


CsrfProtected = Depends(require_csrf)


def app_settings(request: Request) -> Settings:
    return request.app.state.settings


AppSettings = Annotated[Settings, Depends(app_settings)]
