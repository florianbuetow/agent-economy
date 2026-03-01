# Phase 7 — Application Assembly

## Working Directory

```
services/reputation/
```

---

## File 1: `src/reputation_service/app.py`

FastAPI application factory. Wires everything together.

### `create_app() -> FastAPI`

1. Load settings via `get_settings()`
2. Create `FastAPI` instance with title, version, lifespan
3. Register exception handlers via `register_exception_handlers(app)`
4. Register Starlette HTTP exception handler for 405 → `METHOD_NOT_ALLOWED`
5. Register FastAPI `RequestValidationError` handler → 422 with standard envelope
6. Include routers: `health.router` (tags=["Operations"]), `feedback.router` (tags=["Feedback"])
7. Add `RequestValidationMiddleware` with `max_body_size` from settings

### Starlette HTTP Exception Handler

```python
async def _handle_starlette_http(_request, exc):
    if exc.status_code == 405:
        return JSONResponse(status_code=405, content={
            "error": "METHOD_NOT_ALLOWED",
            "message": "Method not allowed",
            "details": {},
        })
    # Generic fallback for other Starlette HTTP exceptions
    return JSONResponse(status_code=exc.status_code, content={
        "error": str(exc.detail),
        "message": str(exc.detail),
        "details": {},
    })
```

### Request Validation Error Handler

```python
async def _handle_validation(_request, _exc):
    return JSONResponse(status_code=422, content={
        "error": "VALIDATION_ERROR",
        "message": "Request validation failed",
        "details": {},
    })
```

### Type Casting

Use `typing.cast` to satisfy type checkers when registering exception handlers with `app.add_exception_handler()`. Define an `ExceptionHandler` type alias under `TYPE_CHECKING`.

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
just run
# In another terminal:
curl -s http://localhost:8004/health | python3 -m json.tool
```

Expected health response:
```json
{
    "status": "ok",
    "uptime_seconds": 1.234,
    "started_at": "2026-02-28T...",
    "total_feedback": 0
}
```

```bash
just stop  # or Ctrl-C
```

Also verify 405 handling:
```bash
curl -s -X POST http://localhost:8004/health | python3 -m json.tool
```

Expected:
```json
{
    "error": "METHOD_NOT_ALLOWED",
    "message": "Method not allowed",
    "details": {}
}
```
