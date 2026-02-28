"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from observatory_service.config import get_settings
from observatory_service.core.exceptions import register_exception_handlers
from observatory_service.core.lifespan import lifespan
from observatory_service.routers import health


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI instance with all routers registered.
    """
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.service.name} Service",
        version=settings.service.version,
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.include_router(health.router, tags=["Operations"])

    return app
