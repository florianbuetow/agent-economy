"""Reputation domain endpoints — feedback."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

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
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.submit_feedback(data)
    return JSONResponse(status_code=201, content=result)


@router.get("/feedback/count")
async def count_feedback() -> JSONResponse:
    """Count total feedback rows."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    count = state.db_reader.count_feedback()
    return JSONResponse(status_code=200, content={"count": count})


@router.get("/feedback/{feedback_id}")
async def get_feedback(feedback_id: str) -> JSONResponse:
    """Get feedback by ID."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    feedback = state.db_reader.get_feedback(feedback_id)
    if feedback is None:
        raise ServiceError(
            error="feedback_not_found",
            message="Feedback not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=feedback)


@router.get("/feedback")
async def list_feedback(request: Request) -> JSONResponse:
    """List feedback by task_id or agent_id query parameter."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="method_not_allowed",
            message="Method not allowed",
            status_code=405,
            details={},
        )

    task_id = request.query_params.get("task_id")
    agent_id = request.query_params.get("agent_id")
    if task_id is not None:
        items = state.db_reader.get_feedback_by_task(task_id)
    elif agent_id is not None:
        items = state.db_reader.get_feedback_by_agent(agent_id)
    else:
        items = []
    return JSONResponse(status_code=200, content={"feedback": items})
