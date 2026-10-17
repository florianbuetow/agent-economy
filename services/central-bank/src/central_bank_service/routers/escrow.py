"""Escrow endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from central_bank_service.core.state import get_app_state
from central_bank_service.financial_responses import validate_operation_id
from central_bank_service.logging import get_logger
from central_bank_service.monetary import require_positive_money, require_worker_percentage
from central_bank_service.routers.helpers import (
    parse_json_body,
    verify_jws_token,
    verify_platform_signature,
)

router = APIRouter()


def _required_payload_string(payload: dict[str, object], field: str) -> str:
    value = payload.get(field)
    if not isinstance(value, str) or not value:
        raise ServiceError("invalid_payload", f"Missing or invalid {field}", 400, {})
    return value


def _required_operation_id(payload: dict[str, object]) -> str:
    try:
        return validate_operation_id(payload.get("operation_id"))
    except ValueError as exc:
        raise ServiceError("invalid_operation_id", str(exc), 400, {}) from exc


def _reject_unknown_payload_fields(payload: dict[str, object], allowed: set[str]) -> None:
    unknown = sorted(set(payload) - allowed)
    if unknown:
        raise ServiceError(
            "invalid_payload",
            "JWS payload contains unsupported fields",
            400,
            {"fields": unknown},
        )


# === POST /escrow/lock — Lock Funds in Escrow (Agent-signed) ===


@router.post("/escrow/lock", status_code=201)
async def escrow_lock(request: Request) -> JSONResponse:
    """Lock funds in escrow. Requires agent's own JWS signature."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("invalid_jws", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("invalid_jws", "JWS token must be a string", 400, {})

    verified = await verify_jws_token(
        data["token"],
        audience="central-bank",
        resource="POST /escrow/lock",
        allowed_action="escrow_lock",
    )
    payload = verified.payload
    _reject_unknown_payload_fields(
        payload,
        {"action", "operation_id", "agent_id", "amount", "task_id"},
    )

    # Agent must be the one whose funds are locked
    agent_id = payload.get("agent_id")
    if not agent_id or not isinstance(agent_id, str):
        raise ServiceError("invalid_payload", "Missing agent_id in JWS payload", 400, {})

    if verified.header.kid != agent_id:
        raise ServiceError(
            "forbidden",
            "You can only lock your own funds",
            403,
            {},
        )

    amount = require_positive_money(payload.get("amount"), "amount")

    task_id = payload.get("task_id")
    if not task_id or not isinstance(task_id, str):
        raise ServiceError("invalid_payload", "Missing task_id in JWS payload", 400, {})
    operation_id = _required_operation_id(payload)

    state = get_app_state()
    if state.ledger is None:
        raise ServiceError(
            error="service_not_ready",
            message="Ledger not initialized",
            status_code=503,
            details={},
        )

    result = await state.ledger.escrow_lock(agent_id, amount, task_id, operation_id)

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
    """Release escrowed funds to recipient. Platform-signed, verified locally."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("invalid_jws", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("invalid_jws", "JWS token must be a string", 400, {})

    verified = verify_platform_signature(
        data["token"],
        audience="central-bank",
        resource=f"POST /escrow/{escrow_id}/release",
        allowed_action="escrow_release",
    )
    payload = verified.payload
    _reject_unknown_payload_fields(
        payload,
        {
            "action",
            "operation_id",
            "escrow_id",
            "recipient_account_id",
            "source_reference",
        },
    )
    operation_id = _required_operation_id(payload)
    source_reference = _required_payload_string(payload, "source_reference")

    recipient_account_id = payload.get("recipient_account_id")
    if not recipient_account_id or not isinstance(recipient_account_id, str):
        raise ServiceError(
            "invalid_payload",
            "Missing recipient_account_id in JWS payload",
            400,
            {},
        )

    payload_escrow_id = payload.get("escrow_id")
    if payload_escrow_id is not None and payload_escrow_id != escrow_id:
        raise ServiceError(
            "payload_mismatch",
            "JWS payload escrow_id does not match URL",
            400,
            {},
        )

    state = get_app_state()
    if state.ledger is None:
        raise ServiceError(
            error="service_not_ready",
            message="Ledger not initialized",
            status_code=503,
            details={},
        )

    result = await state.ledger.escrow_release(
        escrow_id,
        recipient_account_id,
        operation_id,
        source_reference,
    )

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
    """Split escrowed funds between worker and poster. Platform-signed, verified locally."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("invalid_jws", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("invalid_jws", "JWS token must be a string", 400, {})

    verified = verify_platform_signature(
        data["token"],
        audience="central-bank",
        resource=f"POST /escrow/{escrow_id}/split",
        allowed_action="escrow_split",
    )
    payload = verified.payload
    _reject_unknown_payload_fields(
        payload,
        {
            "action",
            "operation_id",
            "escrow_id",
            "worker_account_id",
            "worker_pct",
            "poster_account_id",
            "source_reference",
        },
    )
    operation_id = _required_operation_id(payload)
    source_reference = _required_payload_string(payload, "source_reference")

    worker_account_id = payload.get("worker_account_id")
    if not worker_account_id or not isinstance(worker_account_id, str):
        raise ServiceError(
            "invalid_payload",
            "Missing worker_account_id in JWS payload",
            400,
            {},
        )

    poster_account_id = payload.get("poster_account_id")
    if not poster_account_id or not isinstance(poster_account_id, str):
        raise ServiceError(
            "invalid_payload",
            "Missing poster_account_id in JWS payload",
            400,
            {},
        )

    worker_pct = require_worker_percentage(payload.get("worker_pct"))

    payload_escrow_id = payload.get("escrow_id")
    if payload_escrow_id is not None and payload_escrow_id != escrow_id:
        raise ServiceError(
            "payload_mismatch",
            "JWS payload escrow_id does not match URL",
            400,
            {},
        )

    state = get_app_state()
    if state.ledger is None:
        raise ServiceError(
            error="service_not_ready",
            message="Ledger not initialized",
            status_code=503,
            details={},
        )

    result = await state.ledger.escrow_split(
        escrow_id,
        worker_account_id,
        worker_pct,
        poster_account_id,
        operation_id,
        source_reference,
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
    raise ServiceError("method_not_allowed", "Method not allowed", 405, {})
