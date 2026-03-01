# Phase 5 — Routers: Health, Identity, Bank

## Working Directory

All paths relative to `services/db-gateway/`.

---

## File 1: `src/db_gateway_service/routers/helpers.py`

Create this file. Shared helpers used by all domain routers.

```python
"""Shared request parsing and validation helpers for all routers."""

from __future__ import annotations

import json
from typing import Any

from service_commons.exceptions import ServiceError

# Required event fields
EVENT_REQUIRED_FIELDS: list[str] = [
    "event_source",
    "event_type",
    "timestamp",
    "summary",
    "payload",
]


def parse_json_body(body: bytes) -> dict[str, Any]:
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


def validate_required_fields(data: dict[str, Any], fields: list[str]) -> None:
    """Validate that all required fields exist, are not null, and are not empty strings."""
    for field_name in fields:
        value = data.get(field_name)
        if value is None:
            raise ServiceError(
                "MISSING_FIELD",
                f"Missing required field: {field_name}",
                400,
                {"field": field_name},
            )
        if isinstance(value, str) and not value.strip():
            raise ServiceError(
                "MISSING_FIELD",
                f"Field cannot be empty: {field_name}",
                400,
                {"field": field_name},
            )


def validate_event(data: dict[str, Any]) -> None:
    """Validate that 'event' field exists and has all required sub-fields."""
    event = data.get("event")
    if event is None:
        raise ServiceError(
            "MISSING_FIELD",
            "Missing required field: event",
            400,
            {"field": "event"},
        )
    if not isinstance(event, dict):
        raise ServiceError(
            "MISSING_FIELD",
            "Field 'event' must be an object",
            400,
            {"field": "event"},
        )
    validate_required_fields(event, EVENT_REQUIRED_FIELDS)


def validate_positive_integer(data: dict[str, Any], field_name: str) -> None:
    """Validate that a field is a positive integer."""
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
        raise ServiceError(
            "INVALID_AMOUNT",
            f"Field '{field_name}' must be a positive integer",
            400,
            {"field": field_name},
        )


def validate_non_negative_integer(data: dict[str, Any], field_name: str) -> None:
    """Validate that a field is a non-negative integer."""
    value = data.get(field_name)
    if not isinstance(value, int) or isinstance(value, bool) or value < 0:
        raise ServiceError(
            "INVALID_AMOUNT",
            f"Field '{field_name}' must be a non-negative integer",
            400,
            {"field": field_name},
        )
```

---

## File 2: `src/db_gateway_service/routers/health.py`

Create this file:

```python
"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from db_gateway_service.core.state import get_app_state
from db_gateway_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    database_size_bytes = 0
    total_events = 0
    if state.db_writer is not None:
        database_size_bytes = state.db_writer.get_database_size_bytes()
        total_events = state.db_writer.get_total_events()
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        database_size_bytes=database_size_bytes,
        total_events=total_events,
    )
```

---

## File 3: `src/db_gateway_service/routers/identity.py`

Create this file:

```python
"""Identity domain endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
    validate_required_fields,
)

router = APIRouter(prefix="/identity", tags=["Identity"])


@router.post("/agents", status_code=201)
async def register_agent(request: Request) -> JSONResponse:
    """Register a new agent identity."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(data, ["agent_id", "name", "public_key", "registered_at"])
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.register_agent(data)
    return JSONResponse(status_code=201, content=result)
```

---

## File 4: `src/db_gateway_service/routers/bank.py`

Create this file. The most complex router — handles accounts, credit, and three escrow operations.

```python
"""Bank domain endpoints — accounts, credit, escrow."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
    validate_non_negative_integer,
    validate_positive_integer,
    validate_required_fields,
)

router = APIRouter(prefix="/bank", tags=["Bank"])


@router.post("/accounts", status_code=201)
async def create_account(request: Request) -> JSONResponse:
    """Create a bank account with optional initial credit."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(data, ["account_id", "created_at"])
    validate_event(data)

    # balance must be present and non-negative
    validate_non_negative_integer(data, "balance")

    # If balance > 0, initial_credit must be provided
    if data["balance"] > 0:
        initial_credit = data.get("initial_credit")
        if initial_credit is None or not isinstance(initial_credit, dict):
            from service_commons.exceptions import ServiceError  # noqa: PLC0415

            raise ServiceError(
                "MISSING_FIELD",
                "initial_credit required when balance > 0",
                400,
                {"field": "initial_credit"},
            )
        validate_required_fields(initial_credit, ["tx_id", "amount", "reference", "timestamp"])
        validate_positive_integer(initial_credit, "amount")

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.create_account(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/credit")
async def credit_account(request: Request) -> JSONResponse:
    """Credit an account."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(data, ["tx_id", "account_id", "amount", "reference", "timestamp"])
    validate_event(data)
    validate_positive_integer(data, "amount")

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.credit_account(data)
    return JSONResponse(status_code=200, content=result)


@router.post("/escrow/lock", status_code=201)
async def escrow_lock(request: Request) -> JSONResponse:
    """Lock funds in escrow."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        ["escrow_id", "payer_account_id", "amount", "task_id", "created_at", "tx_id"],
    )
    validate_event(data)
    validate_positive_integer(data, "amount")

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.escrow_lock(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/escrow/release")
async def escrow_release(request: Request) -> JSONResponse:
    """Release escrowed funds to a recipient."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        ["escrow_id", "recipient_account_id", "tx_id", "resolved_at"],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.escrow_release(data)
    return JSONResponse(status_code=200, content=result)


@router.post("/escrow/split")
async def escrow_split(request: Request) -> JSONResponse:
    """Split escrowed funds between worker and poster."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "escrow_id",
            "worker_account_id",
            "poster_account_id",
            "worker_tx_id",
            "poster_tx_id",
            "resolved_at",
        ],
    )
    validate_event(data)
    validate_non_negative_integer(data, "worker_amount")
    validate_non_negative_integer(data, "poster_amount")

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.escrow_split(data)
    return JSONResponse(status_code=200, content=result)
```

---

## Verification

```bash
cd services/db-gateway && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.
