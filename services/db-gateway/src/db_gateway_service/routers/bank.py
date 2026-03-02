"""Bank domain endpoints — accounts, credit, escrow."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

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
            raise ServiceError(
                "missing_field",
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
