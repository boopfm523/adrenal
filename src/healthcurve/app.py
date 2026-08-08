"""FastAPI application factory.

Health endpoints deliberately return a bare status. Threat model T2 and plan section 7
both require that liveness and readiness expose no health data, no counts, and no
version strings -- they are reachable from the public edge and must reveal nothing
about the record or the deployment.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from healthcurve.config import Environment, Settings, get_settings
from healthcurve.logging import configure_logging

API_PREFIX = "/api/v1"


class NoStoreJSONResponse(JSONResponse):
    """Health-bearing responses must not be cached (threat model T7, shared devices)."""

    def init_headers(self, headers: Any = None) -> None:
        super().init_headers(headers)
        self.raw_headers.append((b"cache-control", b"no-store"))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    configure_logging(json_output=settings.environment is not Environment.DEV)

    is_prod = settings.environment is Environment.PROD
    app = FastAPI(
        title="HealthCurve",
        version="0.1.0",
        # T2: no interactive docs or schema on the public production surface.
        docs_url=None if is_prod else "/docs",
        redoc_url=None,
        openapi_url=None if is_prod else f"{API_PREFIX}/openapi.json",
        default_response_class=NoStoreJSONResponse,
    )

    @app.get("/health/live", tags=["health"])
    async def health_live() -> dict[Literal["status"], str]:
        """Process is up. Returns nothing else, by design."""
        return {"status": "ok"}

    @app.get("/health/ready", tags=["health"])
    async def health_ready() -> dict[Literal["status"], str]:
        """Dependencies are reachable. Returns nothing else, by design."""
        return {"status": "ok"}

    return app
