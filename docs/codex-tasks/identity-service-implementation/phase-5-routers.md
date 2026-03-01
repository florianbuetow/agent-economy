# Phase 5 — Routers

## Working Directory

All paths relative to `services/identity/`.

---

## File 1: `src/identity_service/routers/health.py`

Create this file:

```python
"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from identity_service.core.state import get_app_state
from identity_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    registered_agents = 0
    if state.registry is not None:
        registered_agents = state.registry.count_agents()
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        registered_agents=registered_agents,
    )
```

---

## File 2: `src/identity_service/routers/agents.py`

Create this file. This is the longest router file.

**CRITICAL — Route order matters.** Routes for `/agents/register` and `/agents/verify` MUST be defined BEFORE `/agents/{agent_id}`, otherwise GET requests to `/agents/register` would match the parameterized route instead of returning 405.

```python
"""Agent registration, verification, and lookup endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from identity_service.core.state import get_app_state

router = APIRouter()


# ---------------------------------------------------------------------------
# Helper: JSON body parsing and field validation
# ---------------------------------------------------------------------------


def _parse_json_body(body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JSON",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "INVALID_JSON",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


def _validate_required_fields(data: dict[str, Any], fields: list[str]) -> None:
    """Validate that all required fields exist and are not null."""
    for field_name in fields:
        if field_name not in data or data[field_name] is None:
            raise ServiceError(
                "MISSING_FIELD",
                f"Missing required field: {field_name}",
                400,
                {"field": field_name},
            )


def _validate_string_fields(data: dict[str, Any], fields: list[str]) -> None:
    """Validate that specified fields are strings."""
    for field_name in fields:
        if not isinstance(data[field_name], str):
            raise ServiceError(
                "INVALID_FIELD_TYPE",
                f"Field '{field_name}' must be a string",
                400,
                {"field": field_name},
            )


# ---------------------------------------------------------------------------
# POST /agents/register — MUST be defined BEFORE /agents/{agent_id}
# ---------------------------------------------------------------------------


@router.post("/agents/register", status_code=201)
async def register_agent(request: Request) -> JSONResponse:
    """Register a new agent identity."""
    body = await request.body()
    data = _parse_json_body(body)
    _validate_required_fields(data, ["name", "public_key"])
    _validate_string_fields(data, ["name", "public_key"])

    state = get_app_state()
    if state.registry is None:
        msg = "Registry not initialized"
        raise RuntimeError(msg)

    result = state.registry.register_agent(data["name"], data["public_key"])
    return JSONResponse(status_code=201, content=result)


# ---------------------------------------------------------------------------
# POST /agents/verify — MUST be defined BEFORE /agents/{agent_id}
# ---------------------------------------------------------------------------


@router.post("/agents/verify")
async def verify_signature(request: Request) -> dict[str, object]:
    """Verify an agent's signature on a payload."""
    body = await request.body()
    data = _parse_json_body(body)
    _validate_required_fields(data, ["agent_id", "payload", "signature"])
    _validate_string_fields(data, ["agent_id", "payload", "signature"])

    state = get_app_state()
    if state.registry is None:
        msg = "Registry not initialized"
        raise RuntimeError(msg)

    return state.registry.verify_signature(
        data["agent_id"],
        data["payload"],
        data["signature"],
    )


# ---------------------------------------------------------------------------
# Method-not-allowed: /agents/register and /agents/verify
#
# Without these, GET /agents/register would match GET /agents/{agent_id}
# with agent_id="register" and return 404 instead of 405.
# ---------------------------------------------------------------------------


@router.api_route(
    "/agents/register",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def register_method_not_allowed(request: Request) -> None:
    """Reject wrong methods on /agents/register."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


@router.api_route(
    "/agents/verify",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def verify_method_not_allowed(request: Request) -> None:
    """Reject wrong methods on /agents/verify."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})


# ---------------------------------------------------------------------------
# GET /agents — list all agents (defined BEFORE parameterized route)
# ---------------------------------------------------------------------------


@router.get("/agents")
async def list_agents() -> dict[str, list[dict[str, str]]]:
    """List all registered agents (public keys omitted)."""
    state = get_app_state()
    if state.registry is None:
        msg = "Registry not initialized"
        raise RuntimeError(msg)

    agents = state.registry.list_agents()
    return {"agents": agents}


# ---------------------------------------------------------------------------
# GET /agents/{agent_id} — lookup single agent (MUST be last)
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, str]:
    """Look up an agent's public identity."""
    state = get_app_state()
    if state.registry is None:
        msg = "Registry not initialized"
        raise RuntimeError(msg)

    agent = state.registry.get_agent(agent_id)
    if agent is None:
        raise ServiceError("AGENT_NOT_FOUND", "Agent not found", 404, {})
    return agent
```

**Design decisions explained:**

1. **Manual JSON parsing** instead of Pydantic model binding: The acceptance tests expect `400 MISSING_FIELD` and `400 INVALID_FIELD_TYPE`, but FastAPI's Pydantic validation returns `422`. Manual parsing gives exact control over error codes.

2. **Route ordering**: Specific paths (`/agents/register`, `/agents/verify`) before parameterized (`/agents/{agent_id}`). FastAPI matches routes top-to-bottom.

3. **Explicit method-not-allowed routes**: `GET /agents/register` would otherwise match `GET /agents/{agent_id}` with `agent_id="register"`, returning 404 instead of the required 405.

4. **Other 405 cases** (`POST /agents`, `POST /health`, `POST /agents/{id}`, etc.) are handled automatically by Starlette's router (returns 405 when path matches but method doesn't), formatted by our `http_exception_handler` in `core/exceptions.py`.

---

## File 3: `src/identity_service/routers/__init__.py`

Overwrite the existing empty file with:

```python
"""API routers."""

from identity_service.routers import agents, health

__all__ = ["agents", "health"]
```

---

## Verification

```bash
cd services/identity && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.
