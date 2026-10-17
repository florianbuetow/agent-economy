"""Bank domain endpoints — accounts, credit, escrow."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from db_gateway_service.core.state import get_app_state
from db_gateway_service.financial_requests import validate_operation_id
from db_gateway_service.routers.helpers import (
    parse_json_body,
    validate_event,
    validate_non_negative_integer,
    validate_positive_integer,
    validate_required_fields,
)

router = APIRouter(prefix="/bank", tags=["Bank"])


def _reject_unknown_fields(data: dict[str, object], allowed: set[str]) -> None:
    unknown = sorted(set(data) - allowed)
    if unknown:
        raise ServiceError(
            "unknown_field",
            "Request contains unsupported fields",
            400,
            {"fields": unknown},
        )


def _reject_unknown_nested_fields(
    data: dict[str, object],
    allowed: set[str],
    parent: str,
) -> None:
    unknown = sorted(f"{parent}.{field}" for field in set(data) - allowed)
    if unknown:
        raise ServiceError(
            "unknown_field",
            "Request contains unsupported fields",
            400,
            {"fields": unknown},
        )


def _require_operation_id(data: dict[str, object]) -> None:
    validate_required_fields(data, ["operation_id"])
    try:
        validate_operation_id(data["operation_id"])
    except ValueError as exc:
        raise ServiceError(
            "invalid_operation_id",
            "operation_id must be a canonical op-UUID string",
            400,
            {"field": "operation_id"},
        ) from exc


def _validate_worker_pct(data: dict[str, object]) -> None:
    value = data.get("worker_pct")
    if isinstance(value, bool) or not isinstance(value, int) or not 0 <= value <= 100:
        raise ServiceError(
            "invalid_percentage",
            "worker_pct must be an integer from 0 through 100",
            400,
            {"field": "worker_pct"},
        )


@router.post("/accounts", status_code=201)
async def create_account(request: Request) -> JSONResponse:
    """Create a bank account with optional initial credit."""
    body = await request.body()
    data = parse_json_body(body)
    _reject_unknown_fields(
        data,
        {"operation_id", "account_id", "balance", "created_at", "initial_credit", "event"},
    )
    validate_required_fields(data, ["account_id", "created_at"])
    validate_event(data)

    # balance must be present and non-negative
    validate_non_negative_integer(data, "balance")

    # If balance > 0, initial_credit must be provided
    if data["balance"] > 0:
        _require_operation_id(data)
        initial_credit = data.get("initial_credit")
        if initial_credit is None or not isinstance(initial_credit, dict):
            raise ServiceError(
                "missing_field",
                "initial_credit required when balance > 0",
                400,
                {"field": "initial_credit"},
            )
        validate_required_fields(initial_credit, ["tx_id", "amount", "reference", "timestamp"])
        _reject_unknown_nested_fields(
            initial_credit,
            {"tx_id", "amount", "reference", "timestamp"},
            "initial_credit",
        )
        validate_positive_integer(initial_credit, "amount")

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.create_account(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/credit")
async def credit_account(request: Request) -> JSONResponse:
    """Credit an account."""
    body = await request.body()
    data = parse_json_body(body)
    _reject_unknown_fields(
        data,
        {"operation_id", "tx_id", "account_id", "amount", "reference", "timestamp", "event"},
    )
    validate_required_fields(
        data,
        ["operation_id", "tx_id", "account_id", "amount", "reference", "timestamp"],
    )
    _require_operation_id(data)
    validate_event(data)
    validate_positive_integer(data, "amount")

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.credit_account(data)
    return JSONResponse(status_code=200, content=result)


@router.post("/escrow/lock", status_code=201)
async def escrow_lock(request: Request) -> JSONResponse:
    """Lock funds in escrow."""
    body = await request.body()
    data = parse_json_body(body)
    _reject_unknown_fields(
        data,
        {
            "operation_id",
            "escrow_id",
            "payer_account_id",
            "amount",
            "task_id",
            "created_at",
            "tx_id",
            "event",
        },
    )
    validate_required_fields(
        data,
        [
            "operation_id",
            "escrow_id",
            "payer_account_id",
            "amount",
            "task_id",
            "created_at",
            "tx_id",
        ],
    )
    _require_operation_id(data)
    validate_event(data)
    validate_positive_integer(data, "amount")

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.escrow_lock(data)
    return JSONResponse(status_code=201, content=result)


@router.post("/escrow/release")
async def escrow_release(request: Request) -> JSONResponse:
    """Release escrowed funds to a recipient."""
    body = await request.body()
    data = parse_json_body(body)
    _reject_unknown_fields(
        data,
        {
            "operation_id",
            "escrow_id",
            "recipient_account_id",
            "source_reference",
            "tx_id",
            "resolved_at",
            "event",
        },
    )
    validate_required_fields(
        data,
        [
            "operation_id",
            "escrow_id",
            "recipient_account_id",
            "source_reference",
            "tx_id",
            "resolved_at",
        ],
    )
    _require_operation_id(data)
    validate_event(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.escrow_release(data)
    return JSONResponse(status_code=200, content=result)


@router.post("/escrow/split")
async def escrow_split(request: Request) -> JSONResponse:
    """Split escrowed funds between worker and poster."""
    body = await request.body()
    data = parse_json_body(body)
    _reject_unknown_fields(
        data,
        {
            "operation_id",
            "escrow_id",
            "worker_account_id",
            "poster_account_id",
            "worker_pct",
            "source_reference",
            "worker_tx_id",
            "poster_tx_id",
            "resolved_at",
            "event",
        },
    )
    validate_required_fields(
        data,
        [
            "operation_id",
            "escrow_id",
            "worker_account_id",
            "poster_account_id",
            "worker_pct",
            "source_reference",
            "worker_tx_id",
            "poster_tx_id",
            "resolved_at",
        ],
    )
    _require_operation_id(data)
    validate_event(data)
    _validate_worker_pct(data)

    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.escrow_split(data)
    return JSONResponse(status_code=200, content=result)


@router.get("/accounts/count")
async def count_accounts() -> JSONResponse:
    """Count total bank accounts."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    count = state.db_reader.count_accounts()
    return JSONResponse(status_code=200, content={"count": count})


@router.get("/accounts/{account_id}")
async def get_account(account_id: str) -> JSONResponse:
    """Get a bank account by ID."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    account = state.db_reader.get_account(account_id)
    if account is None:
        raise ServiceError(
            error="account_not_found",
            message="Account not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=account)


@router.get("/accounts/{account_id}/transactions")
async def get_transactions(account_id: str) -> JSONResponse:
    """Get transaction history for an account."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    transactions = state.db_reader.get_transactions(account_id)
    return JSONResponse(status_code=200, content={"transactions": transactions})


@router.get("/escrow/total-locked")
async def total_escrowed() -> JSONResponse:
    """Get total locked escrow amount."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    total = state.db_reader.total_escrowed()
    return JSONResponse(status_code=200, content={"total": total})


@router.get("/escrow/{escrow_id}")
async def get_escrow(escrow_id: str) -> JSONResponse:
    """Get an escrow record by ID."""
    if escrow_id in {"lock", "release", "split"}:
        raise ServiceError(
            error="method_not_allowed",
            message="Method not allowed",
            status_code=405,
            details={},
        )

    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbReader not initialized",
            status_code=503,
            details={},
        )

    escrow = state.db_reader.get_escrow(escrow_id)
    if escrow is None:
        raise ServiceError(
            error="escrow_not_found",
            message="Escrow not found",
            status_code=404,
            details={},
        )
    return JSONResponse(status_code=200, content=escrow)
