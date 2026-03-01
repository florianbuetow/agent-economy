"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from task_board_service.config import get_settings
from task_board_service.core.exceptions import register_exception_handlers
from task_board_service.core.lifespan import lifespan
from task_board_service.core.middleware import RequestValidationMiddleware
from task_board_service.routers import assets, bids, health, tasks


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
    app.include_router(tasks.router, tags=["Tasks"])
    app.include_router(bids.router, tags=["Bids"])
    app.include_router(assets.router, tags=["Assets"])

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )

    return app
