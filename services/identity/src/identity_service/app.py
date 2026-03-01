"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from identity_service.config import get_settings
from identity_service.core.exceptions import register_exception_handlers
from identity_service.core.lifespan import lifespan
from identity_service.core.middleware import RequestValidationMiddleware
from identity_service.routers import agents, health


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
    app.include_router(agents.router, tags=["Agents"])

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )

    return app
