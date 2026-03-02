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
