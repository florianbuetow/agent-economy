"""Agent registration, verification, and lookup endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from identity_service.core.state import get_app_state
from identity_service.services.validation import (
    parse_json_body,
    validate_required_fields,
    validate_string_fields,
)

router = APIRouter()


# ---------------------------------------------------------------------------
# POST /agents/register — MUST be defined BEFORE /agents/{agent_id}
# ---------------------------------------------------------------------------


@router.post("/agents/register", status_code=201)
async def register_agent(request: Request) -> JSONResponse:
    """Register a new agent identity."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(data, ["name", "public_key"])
    validate_string_fields(data, ["name", "public_key"])

    state = get_app_state()
    if state.registry is None:
        raise ServiceError(
            error="service_not_ready",
            message="Registry not initialized",
            status_code=503,
            details={},
        )

    result = await state.registry.register_agent(data["name"], data["public_key"])

    return JSONResponse(status_code=201, content=result)


# ---------------------------------------------------------------------------
# POST /agents/verify — MUST be defined BEFORE /agents/{agent_id}
# ---------------------------------------------------------------------------


@router.post("/agents/verify")
async def verify_signature(request: Request) -> dict[str, object]:
    """Verify an agent's signature on a payload."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(data, ["agent_id", "payload", "signature"])
    validate_string_fields(data, ["agent_id", "payload", "signature"])

    state = get_app_state()
    if state.registry is None:
        raise ServiceError(
            error="service_not_ready",
            message="Registry not initialized",
            status_code=503,
            details={},
        )

    return await state.registry.verify_signature(
        data["agent_id"],
        data["payload"],
        data["signature"],
    )


@router.post("/agents/verify-jws")
async def verify_jws(request: Request) -> dict[str, object]:
    """Verify a JWS compact token."""
    body = await request.body()
    data = parse_json_body(body)
    validate_required_fields(data, ["token"])
    validate_string_fields(data, ["token"])

    state = get_app_state()
    if state.registry is None:
        raise ServiceError(
            error="service_not_ready",
            message="Registry not initialized",
            status_code=503,
            details={},
        )

    return await state.registry.verify_jws(data["token"])


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
async def register_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /agents/register."""
    raise ServiceError("method_not_allowed", "Method not allowed", 405, {})


@router.api_route(
    "/agents/verify",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def verify_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /agents/verify."""
    raise ServiceError("method_not_allowed", "Method not allowed", 405, {})


@router.api_route(
    "/agents/verify-jws",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def verify_jws_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /agents/verify-jws."""
    raise ServiceError("method_not_allowed", "Method not allowed", 405, {})


# ---------------------------------------------------------------------------
# GET /agents — list all agents (defined BEFORE parameterized route)
# ---------------------------------------------------------------------------


@router.get("/agents")
async def list_agents() -> dict[str, list[dict[str, str]]]:
    """List all registered agents (public keys omitted)."""
    state = get_app_state()
    if state.registry is None:
        raise ServiceError(
            error="service_not_ready",
            message="Registry not initialized",
            status_code=503,
            details={},
        )

    agents = await state.registry.list_agents()
    return {"agents": agents}


# ---------------------------------------------------------------------------
# GET /agents/{agent_id} — lookup single agent (MUST be last)
# ---------------------------------------------------------------------------


@router.get("/agents/{agent_id}")
async def get_agent(agent_id: str) -> dict[str, str]:
    """Look up an agent's public identity."""
    state = get_app_state()
    if state.registry is None:
        raise ServiceError(
            error="service_not_ready",
            message="Registry not initialized",
            status_code=503,
            details={},
        )

    agent = await state.registry.get_agent(agent_id)
    if agent is None:
        raise ServiceError("agent_not_found", "Agent not found", 404, {})
    return agent
