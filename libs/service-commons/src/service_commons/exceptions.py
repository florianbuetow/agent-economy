"""
Shared exception primitives and handlers for services.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from fastapi.responses import JSONResponse

if TYPE_CHECKING:
    from collections.abc import Callable, Coroutine
    from logging import Logger
    from typing import Any

    from fastapi import FastAPI, Request
    from starlette.requests import Request as StarletteRequest

    ExceptionHandler = Callable[
        [StarletteRequest, Exception],
        Coroutine[Any, Any, JSONResponse],
    ]
    ServiceErrorHandler = Callable[[Request, "ServiceError"], Coroutine[Any, Any, JSONResponse]]
    UnhandledExceptionHandler = Callable[[Request, Exception], Coroutine[Any, Any, JSONResponse]]
    LoggerFactory = Callable[[], Logger]


class ServiceError(Exception):
    """
    Base exception for service errors.

    Attributes:
        error: Machine-readable error code
        message: Human-readable description
        status_code: HTTP status code
        details: Additional context
    """

    def __init__(
        self,
        error: str,
        message: str,
        status_code: int,
        details: dict[str, object] | None,
    ) -> None:
        self.error = error
        self.message = message
        self.status_code = status_code
        if details is None:
            self.details: dict[str, object] = {}
        else:
            self.details = details
        super().__init__(message)


def create_exception_handlers(
    logger_factory: LoggerFactory,
) -> tuple[ServiceErrorHandler, UnhandledExceptionHandler]:
    """
    Create service and unhandled exception handlers for a logger factory.

    Args:
        logger_factory: Callable returning the service logger

    Returns:
        Tuple of (service_error_handler, unhandled_exception_handler)
    """

    async def service_error_handler(
        request: Request,
        exc: ServiceError,
    ) -> JSONResponse:
        """Handle ServiceError exceptions."""
        logger = logger_factory()
        logger.warning(
            "Service error",
            extra={
                "error_code": exc.error,
                "error_message": exc.message,
                "status_code": exc.status_code,
                "details": exc.details,
                "path": str(request.url.path),
            },
        )
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": exc.error,
                "message": exc.message,
                "details": exc.details,
            },
        )

    async def unhandled_exception_handler(
        request: Request,
        _exc: Exception,
    ) -> JSONResponse:
        """Handle unexpected exceptions."""
        logger = logger_factory()
        logger.exception(
            "Unhandled exception",
            extra={
                "path": str(request.url.path),
                "method": request.method,
            },
        )
        return JSONResponse(
            status_code=500,
            content={
                "error": "internal_error",
                "message": "An unexpected error occurred",
                "details": {},
            },
        )

    return service_error_handler, unhandled_exception_handler


def register_exception_handlers(
    app: FastAPI,
    service_error_type: type[ServiceError],
    service_error_handler: ServiceErrorHandler,
    unhandled_exception_handler: UnhandledExceptionHandler,
) -> None:
    """
    Register exception handlers on a FastAPI app.

    Args:
        app: FastAPI application instance
        service_error_type: Service error class to register
        service_error_handler: Service error handler callback
        unhandled_exception_handler: Fallback exception handler callback
    """
    app.add_exception_handler(
        service_error_type,
        cast("ExceptionHandler", service_error_handler),
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)
