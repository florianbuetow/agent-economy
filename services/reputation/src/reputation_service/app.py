"""
FastAPI application factory.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from reputation_service.config import get_settings
from reputation_service.core.exceptions import register_exception_handlers
from reputation_service.core.lifespan import lifespan
from reputation_service.core.middleware import RequestValidationMiddleware
from reputation_service.routers import feedback, health

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from typing import Any

    from starlette.requests import Request as StarletteRequest

    ExceptionHandler = Callable[
        [StarletteRequest, Exception],
        Coroutine[Any, Any, JSONResponse],
    ]


async def _handle_starlette_http(
    _request: object,
    exc: StarletteHTTPException,
) -> JSONResponse:
    """Handle Starlette HTTP exceptions with standard error envelope."""
    if exc.status_code == 405:
        return JSONResponse(
            status_code=405,
            content={
                "error": "METHOD_NOT_ALLOWED",
                "message": "Method not allowed",
                "details": {},
            },
        )
    return JSONResponse(
        status_code=exc.status_code,
        content={
            "error": str(exc.detail),
            "message": str(exc.detail),
            "details": {},
        },
    )


async def _handle_validation(
    _request: object,
    _exc: RequestValidationError,
) -> JSONResponse:
    """Handle request validation errors with standard error envelope."""
    return JSONResponse(
        status_code=422,
        content={
            "error": "VALIDATION_ERROR",
            "message": "Request validation failed",
            "details": {},
        },
    )


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
    app.add_exception_handler(
        StarletteHTTPException,
        cast("ExceptionHandler", _handle_starlette_http),
    )
    app.add_exception_handler(
        RequestValidationError,
        cast("ExceptionHandler", _handle_validation),
    )

    app.include_router(health.router, tags=["Operations"])
    app.include_router(feedback.router, tags=["Feedback"])

    app.add_middleware(
        RequestValidationMiddleware,
        max_body_size=settings.request.max_body_size,
    )

    return app
