"""Escrow endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError
from starlette.concurrency import run_in_threadpool

from central_bank_service.config import get_settings
from central_bank_service.core.state import get_app_state
from central_bank_service.logging import get_logger
from central_bank_service.routers.helpers import (
    parse_json_body,
    require_platform,
    verify_jws_token,
)

router = APIRouter()


# === POST /escrow/lock — Lock Funds in Escrow (Agent-signed) ===


@router.post("/escrow/lock", status_code=201)
async def escrow_lock(request: Request) -> JSONResponse:
    """Lock funds in escrow. Requires agent's own JWS signature."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    verified = await verify_jws_token(data["token"])
    payload = verified["payload"]

    action = payload.get("action")
    if action != "escrow_lock":
        raise ServiceError("INVALID_PAYLOAD", "Invalid action in JWS payload", 400, {})

    # Agent must be the one whose funds are locked
    agent_id = payload.get("agent_id")
    if not agent_id or not isinstance(agent_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing agent_id in JWS payload", 400, {})

    if verified["agent_id"] != agent_id:
        raise ServiceError(
            "FORBIDDEN",
            "You can only lock your own funds",
            403,
            {},
        )

    amount = payload.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        raise ServiceError("INVALID_AMOUNT", "Amount must be a positive integer", 400, {})

    task_id = payload.get("task_id")
    if not task_id or not isinstance(task_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing task_id in JWS payload", 400, {})

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    result = await run_in_threadpool(state.ledger.escrow_lock, agent_id, amount, task_id)
    get_logger(__name__).info(
        "Escrow locked",
        extra={
            "escrow_id": result.get("escrow_id"),
            "payer_account_id": agent_id,
            "amount": amount,
            "task_id": task_id,
        },
    )
    return JSONResponse(status_code=201, content=result)


# === POST /escrow/{escrow_id}/release — Full Payout (Platform-signed) ===


@router.post("/escrow/{escrow_id}/release")
async def escrow_release(request: Request, escrow_id: str) -> dict[str, object]:
    """Release escrowed funds to recipient. Platform-only."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    settings = get_settings()

    verified = await verify_jws_token(data["token"])
    require_platform(verified["agent_id"], settings.platform.agent_id)

    payload = verified["payload"]
    action = payload.get("action")
    if action != "escrow_release":
        raise ServiceError("INVALID_PAYLOAD", "Invalid action in JWS payload", 400, {})

    recipient_account_id = payload.get("recipient_account_id")
    if not recipient_account_id or not isinstance(recipient_account_id, str):
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Missing recipient_account_id in JWS payload",
            400,
            {},
        )

    payload_escrow_id = payload.get("escrow_id")
    if payload_escrow_id is not None and payload_escrow_id != escrow_id:
        raise ServiceError(
            "PAYLOAD_MISMATCH",
            "JWS payload escrow_id does not match URL",
            400,
            {},
        )

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    result = await run_in_threadpool(state.ledger.escrow_release, escrow_id, recipient_account_id)
    get_logger(__name__).info(
        "Escrow released",
        extra={
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_account_id,
            "amount": result.get("amount"),
        },
    )
    return result


# === POST /escrow/{escrow_id}/split — Proportional Split (Platform-signed) ===


@router.post("/escrow/{escrow_id}/split")
async def escrow_split(request: Request, escrow_id: str) -> dict[str, object]:
    """Split escrowed funds between worker and poster. Platform-only."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    settings = get_settings()

    verified = await verify_jws_token(data["token"])
    require_platform(verified["agent_id"], settings.platform.agent_id)

    payload = verified["payload"]
    action = payload.get("action")
    if action != "escrow_split":
        raise ServiceError("INVALID_PAYLOAD", "Invalid action in JWS payload", 400, {})

    worker_account_id = payload.get("worker_account_id")
    if not worker_account_id or not isinstance(worker_account_id, str):
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Missing worker_account_id in JWS payload",
            400,
            {},
        )

    poster_account_id = payload.get("poster_account_id")
    if not poster_account_id or not isinstance(poster_account_id, str):
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Missing poster_account_id in JWS payload",
            400,
            {},
        )

    worker_pct = payload.get("worker_pct")
    if not isinstance(worker_pct, int):
        raise ServiceError("INVALID_PAYLOAD", "worker_pct must be an integer", 400, {})

    payload_escrow_id = payload.get("escrow_id")
    if payload_escrow_id is not None and payload_escrow_id != escrow_id:
        raise ServiceError(
            "PAYLOAD_MISMATCH",
            "JWS payload escrow_id does not match URL",
            400,
            {},
        )

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    result = await run_in_threadpool(
        state.ledger.escrow_split,
        escrow_id,
        worker_account_id,
        worker_pct,
        poster_account_id,
    )
    get_logger(__name__).info(
        "Escrow split",
        extra={
            "escrow_id": escrow_id,
            "worker_account_id": worker_account_id,
            "poster_account_id": poster_account_id,
            "worker_pct": worker_pct,
            "worker_amount": result.get("worker_amount"),
            "poster_amount": result.get("poster_amount"),
        },
    )
    return result


# === Method-not-allowed handlers ===


@router.api_route(
    "/escrow/lock",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def escrow_lock_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /escrow/lock."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
