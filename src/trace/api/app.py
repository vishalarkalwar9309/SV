"""FastAPI application factory for TRACE 2.0.

Provides configurable CORS, dependency injection, and clean error handling.
"""

from __future__ import annotations

import os
from trace.api.routes import router
from trace.application.investigation_service import InvestigationService

from fastapi import FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse


def create_app(service: InvestigationService | None = None) -> FastAPI:
    """Create and configure a FastAPI application instance.

    Args:
        service: Optional InvestigationService instance for testing and dependency injection.
                 Defaults to a fresh InvestigationService if omitted.
    """
    app = FastAPI(
        title="TRACE 2.0 API",
        description="Autonomous Technical Root-Cause & Resolution Engine API",
        version="0.1.0",
    )

    # CORS configuration — default to local frontend origin, configurable via env var
    frontend_origin = os.environ.get("TRACE_FRONTEND_ORIGIN", "http://localhost:3000")
    origins = [origin.strip() for origin in frontend_origin.split(",") if origin.strip()]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    # Store service in app state for dependency injection
    app.state.investigation_service = (
        service if service is not None else InvestigationService()
    )

    # Controlled exception handler to ensure no secrets or stack traces leak on unexpected errors
    @app.exception_handler(Exception)
    async def unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
        if isinstance(exc, HTTPException):
            return JSONResponse(
                status_code=exc.status_code,
                content={"detail": exc.detail},
                headers=exc.headers,
            )
        return JSONResponse(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            content={"detail": "Internal server error"},
        )

    app.include_router(router)
    return app


# Default application instance for ASGI servers (e.g. uvicorn trace.api.app:app)
app = create_app()
