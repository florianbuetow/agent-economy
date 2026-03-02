# Endpoint Error Handling Specification

This document defines the mandatory error handling pattern for all service endpoints. Every endpoint must catch errors explicitly and return a structured JSON response — errors must never be swallowed silently.

## Core Principles

1. **Never swallow errors** — every error must be caught and returned as a structured JSON response with an appropriate HTTP status code.
2. **Consistent format** — all error responses use the same three-field JSON structure across every service.
3. **Consistent success format** — all success responses use a uniform structure.
4. **Fail fast** — invalid input, missing configuration, and unavailable dependencies produce immediate errors, never silent fallbacks.
5. **Explicit context** — the `details` field carries actionable debugging information (IDs, dimensions, thresholds, etc.).
6. **Utility helpers** — response objects are created through shared utility functions, not constructed ad-hoc in each endpoint.

---

## Error Response Format

Every error response is a JSON object with exactly three fields:

```json
{
  "error": "<machine_readable_error_code>",
  "message": "<human-readable description of what went wrong>",
  "details": { "<key>": "<value>" }
}
```

| Field | Type | Description |
|-------|------|-------------|
| `error` | `string` | A stable, machine-readable error code (snake_case). Clients switch on this value. |
| `message` | `string` | A human-readable description. May change between releases — clients must not parse it. |
| `details` | `object` | Additional context. Always an object, never `null`. Empty `{}` when no extra context exists. |

### Error Response Schema

```python
from typing import Any

from pydantic import BaseModel


class ErrorResponse(BaseModel):
    """Standard error response returned by all endpoints on failure."""

    error: str
    """Machine-readable error code (snake_case)."""

    message: str
    """Human-readable error description."""

    details: dict[str, Any]
    """Additional error context. Empty dict when no extra context exists."""
```

### Example Error Responses

**400 — Bad Request (invalid input):**

```json
{
  "error": "unsupported_format",
  "message": "Image format 'image/tiff' is not supported. Use JPEG, PNG, or WebP.",
  "details": {
    "detected_format": "image/tiff",
    "supported_formats": ["image/jpeg", "image/png", "image/webp"]
  }
}
```

**404 — Not Found:**

```json
{
  "error": "not_found",
  "message": "Object 'artwork_042' not found",
  "details": {
    "object_id": "artwork_042"
  }
}
```

**422 — Unprocessable Entity (valid JSON, invalid content):**

```json
{
  "error": "insufficient_features",
  "message": "Query has only 12 features (minimum: 50)",
  "details": {
    "query_features": 12
  }
}
```

**500 — Internal Server Error:**

```json
{
  "error": "internal_error",
  "message": "An unexpected error occurred",
  "details": {}
}
```

**502 — Backend Error (gateway only):**

```json
{
  "error": "backend_error",
  "message": "Embeddings service returned an error",
  "details": {
    "backend": "embeddings",
    "backend_error": "model_error",
    "backend_message": "CUDA out of memory"
  }
}
```

**503 — Service Unavailable:**

```json
{
  "error": "index_not_loaded",
  "message": "Index is not loaded. Call POST /index/load or wait for startup.",
  "details": {
    "index_path": "/data/index"
  }
}
```

---

## Success Response Format

Success responses are endpoint-specific Pydantic models. There is no single wrapper — each endpoint defines its own response schema. The common convention is to include a `processing_time_ms` field for observability.

```json
{
  "embedding": [0.234, -0.891, 0.412],
  "dimension": 768,
  "image_id": "visitor_photo_001",
  "processing_time_ms": 47.3
}
```

### Health Check (all services)

```json
{
  "status": "healthy"
}
```

Status is one of: `"healthy"`, `"degraded"`, `"unhealthy"`.

---

## HTTP Status Code Reference

### Success

| Code | Meaning | When to use |
|------|---------|-------------|
| 200 | OK | Successful operation with response body |
| 204 | No Content | Successful operation with no response body (e.g., PUT/DELETE) |

### Client Errors

| Code | Meaning | When to use |
|------|---------|-------------|
| 400 | Bad Request | Malformed input: invalid base64, bad image data, wrong dimensions, invalid IDs |
| 404 | Not Found | Requested resource does not exist |
| 422 | Unprocessable Entity | Valid JSON but the content cannot be processed (empty index, insufficient features) |

### Server Errors

| Code | Meaning | When to use |
|------|---------|-------------|
| 500 | Internal Server Error | Unexpected/unhandled exceptions |
| 502 | Bad Gateway | Backend service returned an error (gateway only) |
| 503 | Service Unavailable | Service not ready (model not loaded, index not loaded, store not initialized) |
| 504 | Gateway Timeout | Backend service timed out (gateway only) |

---

## The ServiceError Exception

All expected errors are raised as `ServiceError` exceptions. The shared exception handler catches them and converts them to the standard JSON response.

```python
class ServiceError(Exception):
    """Base exception for all service errors.

    Raise this from endpoints and helper functions. The registered
    exception handler converts it to a JSON response automatically.
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
        self.details = details if details is not None else {}
        super().__init__(message)
```

Endpoint code raises `ServiceError` and does **not** construct `JSONResponse` directly:

```python
# CORRECT — raise ServiceError, let the handler format the response
raise ServiceError(
    error="invalid_image",
    message=f"Failed to decode image: {e}",
    status_code=400,
    details=None,
) from e

# WRONG — never construct JSONResponse for errors in endpoint code
return JSONResponse(status_code=400, content={"error": "invalid_image", ...})
```

---

## Exception Handlers (Utility Layer)

Two exception handlers are registered on every FastAPI application. They are the single place where error responses are constructed.

### Handler Factory

```python
from typing import Callable

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse


# Type aliases
LoggerFactory = Callable[[], logging.Logger]
ServiceErrorHandler = Callable[[Request, ServiceError], JSONResponse]
UnhandledExceptionHandler = Callable[[Request, Exception], JSONResponse]


def create_exception_handlers(
    logger_factory: LoggerFactory,
) -> tuple[ServiceErrorHandler, UnhandledExceptionHandler]:
    """Create the two exception handlers for a service.

    Args:
        logger_factory: Callable that returns the service's logger.

    Returns:
        A (service_error_handler, unhandled_exception_handler) tuple.
    """

    async def service_error_handler(
        request: Request,
        exc: ServiceError,
    ) -> JSONResponse:
        """Convert a ServiceError into a structured JSON response."""
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
        """Catch-all for unexpected exceptions. Logs the full traceback
        and returns a generic 500 response — never exposes internals."""
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
```

### Handler Registration

```python
from typing import cast

from fastapi import FastAPI
from starlette.types import ExceptionHandler


def register_exception_handlers(
    app: FastAPI,
    service_error_type: type[ServiceError],
    service_error_handler: ServiceErrorHandler,
    unhandled_exception_handler: UnhandledExceptionHandler,
) -> None:
    """Register exception handlers on a FastAPI application.

    Must be called BEFORE adding routers so that handlers are active
    for all routes.
    """
    app.add_exception_handler(
        service_error_type,
        cast("ExceptionHandler", service_error_handler),
    )
    app.add_exception_handler(Exception, unhandled_exception_handler)
```

### Per-Service Wiring

Each service creates a thin wrapper module that binds the shared handlers to the service's logger:

```python
# src/<service_name>/core/exceptions.py

from service_commons.exceptions import (
    ServiceError,
    create_exception_handlers,
    register_exception_handlers as register_common_handlers,
)

from my_service.logging import get_logger

_service_handler, _unhandled_handler = create_exception_handlers(get_logger)


def register_exception_handlers(app: FastAPI) -> None:
    """Register all exception handlers on the app."""
    register_common_handlers(app, ServiceError, _service_handler, _unhandled_handler)
```

### Registration in the App Factory

```python
# src/<service_name>/app.py

def create_app() -> FastAPI:
    app = FastAPI(title="My Service", version="0.1.0", lifespan=lifespan)

    # Register exception handlers BEFORE adding routes
    register_exception_handlers(app)

    app.include_router(health.router)
    app.include_router(my_endpoints.router)
    return app
```

---

## Endpoint Implementation Pattern

Every endpoint follows this structure:

1. **Validate input** — raise `ServiceError(status_code=400)` on bad input.
2. **Check preconditions** — raise `ServiceError(status_code=503)` if the service is not ready.
3. **Perform the operation** — wrap risky calls in `try/except`, catch specific exceptions, and re-raise as `ServiceError`.
4. **Return the success response** — a typed Pydantic model.

Unexpected exceptions are **not** caught in endpoint code — the `unhandled_exception_handler` catches them and returns a generic 500.

### Complete Example: Simple Endpoint

```python
import time

from fastapi import APIRouter

from service_commons.exceptions import ServiceError

from my_service.config import get_settings
from my_service.core.state import get_app_state
from my_service.schemas import ProcessRequest, ProcessResponse

router = APIRouter()


@router.post("/process", response_model=ProcessResponse)
async def process_item(request: ProcessRequest) -> ProcessResponse:
    """Process an item and return results."""
    start_time = time.perf_counter()
    state = get_app_state()

    # 1. Check preconditions
    if state.model is None:
        raise ServiceError(
            error="model_not_loaded",
            message="Model is not loaded yet",
            status_code=503,
            details=None,
        )

    # 2. Validate input beyond what Pydantic covers
    if request.dimension > state.max_dimension:
        raise ServiceError(
            error="dimension_exceeds_maximum",
            message=f"Requested dimension={request.dimension} exceeds maximum of {state.max_dimension}",
            status_code=400,
            details={
                "max_dimension": state.max_dimension,
                "requested_dimension": request.dimension,
            },
        )

    # 3. Perform the operation — catch specific exceptions only
    try:
        result = state.model.run(request.data)
    except ValueError as e:
        raise ServiceError(
            error="processing_failed",
            message=f"Processing failed: {e}",
            status_code=422,
            details={"reason": str(e)},
        ) from e

    # 4. Return typed success response
    processing_time_ms = (time.perf_counter() - start_time) * 1000
    return ProcessResponse(
        result=result,
        processing_time_ms=round(processing_time_ms, 2),
    )
```

### Complete Example: Endpoint with External Dependency

```python
@router.get("/objects/{object_id}")
async def get_object(object_id: str) -> Response:
    """Retrieve a binary object by ID."""

    # 1. Validate input
    if not VALID_ID_PATTERN.match(object_id):
        raise ServiceError(
            error="invalid_id",
            message=f"Invalid object ID: '{object_id}'. IDs must contain only alphanumeric characters, hyphens, and underscores.",
            status_code=400,
            details={"object_id": object_id},
        )

    # 2. Check preconditions
    state = get_app_state()
    if state.blob_store is None:
        raise ServiceError(
            error="service_unavailable",
            message="Blob store is not initialized",
            status_code=503,
            details={},
        )

    # 3. Perform the operation — catch OS-level errors
    try:
        data = state.blob_store.get(object_id)
    except OSError as exc:
        raise ServiceError(
            error="storage_read_failed",
            message=f"Failed to read object '{object_id}'",
            status_code=503,
            details={
                "object_id": object_id,
                "exception_type": exc.__class__.__name__,
                "reason": str(exc),
            },
        ) from exc

    # 4. Handle "not found" as an explicit error
    if data is None:
        raise ServiceError(
            error="not_found",
            message=f"Object '{object_id}' not found",
            status_code=404,
            details={},
        )

    # 5. Return success
    return Response(content=data, media_type="application/octet-stream")
```

### Complete Example: Gateway Endpoint with Graceful Degradation

When calling optional backend services, the gateway catches backend errors and degrades rather than failing:

```python
@router.post("/identify", response_model=IdentifyResponse)
async def identify_artwork(request: IdentifyRequest) -> IdentifyResponse:
    """Full identification pipeline: embed -> search -> verify."""
    state = get_app_state()
    timing: dict[str, float] = {}

    # Step 1: Critical backend — errors propagate as 502/504
    embedding = await state.embeddings_client.embed(request.image)

    # Step 2: Critical backend — errors propagate as 502/504
    candidates = await state.search_client.search(embedding=embedding, k=5)

    if not candidates:
        return IdentifyResponse(
            success=True,
            match=None,
            message="No matching artwork found",
        )

    # Step 3: Optional backend — errors are caught and degraded
    geometric_scores: dict[str, float] = {}
    degraded = False
    degradation_reason: str | None = None

    try:
        batch_result = await state.geometric_client.match_batch(
            query_image=request.image,
            references=references,
        )
        for result in batch_result.results:
            geometric_scores[result.reference_id] = result.confidence
    except (BackendError, httpx.HTTPError) as e:
        # Do NOT re-raise — degrade gracefully instead
        degraded = True
        degradation_reason = "geometric_backend_unavailable"

    # Step 4: Compute final scores and return
    return IdentifyResponse(
        success=True,
        match=best_match,
        degraded=degraded,
        degradation_reason=degradation_reason,
    )
```

---

## Extracting Validation into Helper Functions

Validation logic that is reused across endpoints should be extracted into helper functions that raise `ServiceError`:

```python
def decode_base64_image(base64_string: str) -> bytes:
    """Decode a base64-encoded image string to bytes.

    Raises:
        ServiceError: 400 decode_error if the string is not valid base64.
    """
    if "," in base64_string:
        base64_string = base64_string.split(",", 1)[1]

    try:
        return base64.b64decode(base64_string)
    except binascii.Error as e:
        raise ServiceError(
            error="decode_error",
            message=f"Invalid Base64 encoding: {e}",
            status_code=400,
            details=None,
        ) from e


def validate_object_id(object_id: str) -> None:
    """Validate that an object ID contains only safe characters.

    Raises:
        ServiceError: 400 invalid_id if the format is invalid.
    """
    if not VALID_ID_PATTERN.match(object_id):
        raise ServiceError(
            error="invalid_id",
            message=f"Invalid object ID: '{object_id}'. IDs must contain only alphanumeric characters, hyphens, and underscores.",
            status_code=400,
            details={"object_id": object_id},
        )
```

These helpers keep endpoint code focused on the happy path while ensuring validation errors are never swallowed.

---

## Rules Summary

| Rule | Rationale |
|------|-----------|
| Every error returns `{"error", "message", "details"}` | Clients depend on a stable, predictable format. |
| `details` is always an object, never `null` | Eliminates null-checks in client code. |
| Raise `ServiceError`, never return `JSONResponse` for errors | Single handler ensures consistent formatting and logging. |
| Catch **specific** exceptions, never bare `except Exception` in endpoints | The unhandled handler is the catch-all — endpoint code must not duplicate it. |
| Always chain exceptions with `from e` | Preserves the original traceback for debugging. |
| Use `422` for "valid JSON but cannot process", `400` for "malformed input" | Matches HTTP semantics and avoids ambiguity. |
| Log every error with structured context | The exception handler logs automatically — endpoint code does not need to duplicate logging for errors it raises. |
| Register handlers **before** adding routes | Ensures handlers are active for all endpoints including those added later. |
| Gateway catches optional-backend errors and degrades | Critical backends propagate errors; optional backends degrade with a penalty. |
| Unexpected exceptions produce a generic 500 — never leak internals | The `unhandled_exception_handler` logs the full traceback server-side but returns only `"internal_error"` to the client. |
