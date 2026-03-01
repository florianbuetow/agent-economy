# Phase 6 — Routers: Board, Reputation, Court

## Working Directory

All paths relative to `services/db-gateway/`.

---

## File 1: `src/db_gateway_service/routers/board.py`

Create this file:

```python
"""Board domain endpoints — tasks, bids, task status, assets."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
    validate_positive_integer,
    validate_required_fields,
)

router = APIRouter(prefix="/board", tags=["Board"])


@router.post("/tasks", status_code=201)
async def create_task(request: Request) -> JSONResponse:
    """Create a new task."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "task_id",
            "poster_id",
            "title",
            "spec",
            "reward",
            "status",
            "bidding_deadline_seconds",
            "deadline_seconds",
            "review_deadline_seconds",
            "bidding_deadline",
            "escrow_id",
            "created_at",
        ],
    )
    validate_event(data)
    validate_positive_integer(data, "reward")

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.create_task(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/bids", status_code=201)
async def submit_bid(request: Request) -> JSONResponse:
    """Submit a bid on a task."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        ["bid_id", "task_id", "bidder_id", "proposal", "submitted_at"],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.submit_bid(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/tasks/{task_id}/status")
async def update_task_status(task_id: str, request: Request) -> JSONResponse:
    """Update a task's status and associated fields."""
    body = await request.body()
    data = parse_json_body(body)

    # Validate updates object
    updates = data.get("updates")
    if updates is None:
        raise ServiceError(
            "MISSING_FIELD",
            "Missing required field: updates",
            400,
            {"field": "updates"},
        )
    if not isinstance(updates, dict):
        raise ServiceError(
            "MISSING_FIELD",
            "Field 'updates' must be an object",
            400,
            {"field": "updates"},
        )
    if len(updates) == 0:
        raise ServiceError(
            "EMPTY_UPDATES",
            "updates object contains no fields",
            400,
            {},
        )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.update_task_status(task_id, data)
    return JSONResponse(status_code=200, content=result)


@router.post("/assets", status_code=201)
async def record_asset(request: Request) -> JSONResponse:
    """Record an asset upload (metadata only)."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "asset_id",
            "task_id",
            "uploader_id",
            "filename",
            "content_type",
            "size_bytes",
            "storage_path",
            "uploaded_at",
        ],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.record_asset(data)
    return JSONResponse(status_code=201, content=result)
```

---

## File 2: `src/db_gateway_service/routers/reputation.py`

Create this file:

```python
"""Reputation domain endpoints — feedback."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
    validate_required_fields,
)

router = APIRouter(prefix="/reputation", tags=["Reputation"])


@router.post("/feedback", status_code=201)
async def submit_feedback(request: Request) -> JSONResponse:
    """Submit feedback for a completed task with optional mutual reveal."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "feedback_id",
            "task_id",
            "from_agent_id",
            "to_agent_id",
            "role",
            "category",
            "rating",
            "submitted_at",
        ],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.submit_feedback(data)
    return JSONResponse(status_code=201, content=result)
```

---

## File 3: `src/db_gateway_service/routers/court.py`

Create this file:

```python
"""Court domain endpoints — claims, rebuttals, rulings."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from db_gateway_service.core.state import get_app_state
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
    validate_required_fields,
)

router = APIRouter(prefix="/court", tags=["Court"])


@router.post("/claims", status_code=201)
async def file_claim(request: Request) -> JSONResponse:
    """File a dispute claim."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "claim_id",
            "task_id",
            "claimant_id",
            "respondent_id",
            "reason",
            "status",
            "filed_at",
        ],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.file_claim(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/rebuttals", status_code=201)
async def submit_rebuttal(request: Request) -> JSONResponse:
    """Submit a rebuttal to a dispute claim."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        ["rebuttal_id", "claim_id", "agent_id", "content", "submitted_at"],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.submit_rebuttal(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/rulings", status_code=201)
async def record_ruling(request: Request) -> JSONResponse:
    """Record a court ruling."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(
        data,
        [
            "ruling_id",
            "claim_id",
            "task_id",
            "worker_pct",
            "summary",
            "judge_votes",
            "ruled_at",
        ],
    )
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        msg = "DbWriter not initialized"
        raise RuntimeError(msg)

    result = state.db_writer.record_ruling(data)
    return JSONResponse(status_code=201, content=result)
```

---

## File 4: `src/db_gateway_service/routers/__init__.py`

Overwrite the existing empty file with:

```python
"""API routers."""

from db_gateway_service.routers import bank, board, court, health, identity, reputation

__all__ = ["bank", "board", "court", "health", "identity", "reputation"]
```

---

## Verification

```bash
cd services/db-gateway && uv run ruff check src/ && uv run ruff format --check src/
```

Must pass with zero errors.
