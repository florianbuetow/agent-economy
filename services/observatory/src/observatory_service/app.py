"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from observatory_service.config import get_settings
from observatory_service.core.exceptions import register_exception_handlers
from observatory_service.core.lifespan import lifespan
from observatory_service.routers import agents, events, health, metrics, quarterly, tasks


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
    app.include_router(metrics.router, prefix="/api", tags=["Metrics"])
    app.include_router(events.router, prefix="/api", tags=["Events"])
    app.include_router(agents.router, prefix="/api", tags=["Agents"])
    app.include_router(tasks.router, prefix="/api", tags=["Tasks"])
    app.include_router(quarterly.router, prefix="/api", tags=["Quarterly"])

    return app
