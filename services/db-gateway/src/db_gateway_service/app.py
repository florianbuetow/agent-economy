"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from db_gateway_service.config import get_settings
from db_gateway_service.core.exceptions import register_exception_handlers
from db_gateway_service.core.lifespan import lifespan
from db_gateway_service.core.middleware import RequestValidationMiddleware
from db_gateway_service.routers import bank, board, court, health, identity, reputation


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
    app.include_router(identity.router)
    app.include_router(bank.router)
    app.include_router(board.router)
    app.include_router(reputation.router)
    app.include_router(court.router)

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )

    return app
