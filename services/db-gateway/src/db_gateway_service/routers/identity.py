"""Identity domain endpoints."""

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
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.register_agent(data)
    return JSONResponse(status_code=201, content=result)


@router.get("/agents/count")
async def count_agents() -> JSONResponse:
    """Count total registered agents."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    count = state.db_reader.count_agents()
    return JSONResponse(status_code=200, content={"count": count})


@router.get("/agents")
async def list_agents(request: Request) -> JSONResponse:
    """List all agents, optionally filtered by public_key query param."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    public_key = request.query_params.get("public_key")
    agents = state.db_reader.list_agents(public_key=public_key)
    return JSONResponse(status_code=200, content={"agents": agents})


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> JSONResponse:
    """Get a single agent by ID."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    agent = state.db_reader.get_agent(agent_id)
    if agent is None:
        raise ServiceError(
            error="agent_not_found",
            message="No agent with this agent_id",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=agent)
