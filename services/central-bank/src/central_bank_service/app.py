"""FastAPI application factory."""

from __future__ import annotations

from fastapi import FastAPI

from central_bank_service.config import get_settings
from central_bank_service.core.exceptions import register_exception_handlers
from central_bank_service.core.lifespan import lifespan
from central_bank_service.core.middleware import RequestValidationMiddleware
from central_bank_service.routers import accounts, escrow, health


def create_app() -> FastAPI:
    """
    Create and configure FastAPI application.

    Returns:
        Configured FastAPI instance with all routers registered
    """
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.service.name} Service",
        version=settings.service.version,
        lifespan=lifespan,
    )

    register_exception_handlers(app)

    app.include_router(health.router, tags=["Operations"])
    app.include_router(accounts.router, tags=["Accounts"])
    app.include_router(escrow.router, tags=["Escrow"])

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )

    return app
