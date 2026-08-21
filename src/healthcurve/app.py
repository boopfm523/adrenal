"""FastAPI application factory.

Health endpoints deliberately return a bare status. Threat model T2 and plan section 7
both require that liveness and readiness expose no health data, no counts, and no
version strings -- they are reachable from the public edge and must reveal nothing
about the record or the deployment.
"""

from __future__ import annotations

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from typing import Any, Literal

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.orm import sessionmaker

from healthcurve.api.routers import (
    analytics,
    auth,
    chat,
    context,
    data_quality,
    doses,
    emergency,
    episodes,
    events,
    garmin,
    labs,
    medications,
    privacy,
    private_documents,
    reports,
    telegram,
    vitals,
)
from healthcurve.config import Environment, Settings, get_settings
from healthcurve.db import build_ai_engine
from healthcurve.logging import configure_logging
from healthcurve.operations.rate_limit import RateLimiter
from healthcurve.operations.telemetry import OperationalEvent, OperationalTelemetry

API_PREFIX = "/api/v1"


class NoStoreJSONResponse(JSONResponse):
    """Health-bearing responses must not be cached (threat model T7, shared devices)."""

    def init_headers(self, headers: Any = None) -> None:
        super().init_headers(headers)
        self.raw_headers.append((b"cache-control", b"no-store"))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json_output=settings.environment is not Environment.DEV)
    ai_engine = build_ai_engine(settings)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncGenerator[None]:
        try:
            yield
        finally:
            ai_engine.dispose()

    is_prod = settings.environment is Environment.PROD
    app = FastAPI(
        title="HealthCurve",
        version="0.1.0",
        # T2: no interactive docs or schema on the public production surface.
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else f"{API_PREFIX}/openapi.json",
        default_response_class=NoStoreJSONResponse,
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.ai_session_factory = sessionmaker(ai_engine, expire_on_commit=False)
    app.state.rate_limiter = RateLimiter(settings.redis_url)
    app.state.telemetry = OperationalTelemetry(settings.redis_url)

    @app.middleware("http")
    async def record_request_errors(request: Request, call_next: Any) -> Any:
        try:
            response = await call_next(request)
        except Exception:
            app.state.telemetry.record(OperationalEvent.REQUEST_ERROR)
            raise
        if response.status_code >= 500:
            app.state.telemetry.record(OperationalEvent.REQUEST_ERROR)
        return response

    @app.get("/health/live", tags=["health"])
    async def health_live() -> dict[Literal["status"], str]:
        """Process is up. Returns nothing else, by design."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def health_ready() -> dict[Literal["status"], str]:
        """Dependencies are reachable. Returns nothing else, by design."""
        return {"status": "ok"}

    for router in (
        auth.router,
        chat.router,
        context.router,
        data_quality.router,
        analytics.router,
        medications.router,
        private_documents.router,
        privacy.router,
        reports.router,
        doses.router,
        events.router,
        episodes.router,
        garmin.router,
        labs.router,
        telegram.router,
        vitals.router,
    ):
        app.include_router(router, prefix=API_PREFIX)

    # The emergency page is mounted at the top level, not under /api/v1: it is a page
    # a person opens in a panic, and it must not depend on the API client or a bundle
    # loading successfully (SAFE-21, ADR-0005).
    app.include_router(emergency.router)

    return app
