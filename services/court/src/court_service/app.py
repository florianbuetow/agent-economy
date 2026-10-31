"""FastAPI application factory."""

from __future__ import annotations

import logging

from fastapi import FastAPI
from service_commons.telemetry import setup_telemetry

from court_service.config import get_settings
from court_service.core.exceptions import register_exception_handlers
from court_service.core.lifespan import lifespan
from court_service.core.middleware import RequestValidationMiddleware
from court_service.routers import disputes, health


def create_app() -> FastAPI:
    """Create and configure the FastAPI application."""
    settings = get_settings()

    app = FastAPI(
        title=f"{settings.service.name} Service",
        version=settings.service.version,
        lifespan=lifespan,
    )

    register_exception_handlers(app)
    setup_telemetry(
        settings.telemetry,
        service_name=settings.service.name,
        app=app,
        log_logger=logging.getLogger(settings.service.name),
    )

    app.include_router(health.router, tags=["Operations"])
    app.include_router(disputes.router, tags=["Disputes"])

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )

    return app
