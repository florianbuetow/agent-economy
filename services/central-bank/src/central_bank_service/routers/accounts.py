"""Account endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError
from starlette.concurrency import run_in_threadpool

from central_bank_service.core.state import get_app_state
from central_bank_service.logging import get_logger
from central_bank_service.routers.helpers import (
    get_platform_agent_id,
    parse_json_body,
    require_account_owner,
    require_platform,
    verify_jws_token,
)

router = APIRouter()


# === POST /accounts — Create Account ===


@router.post("/accounts", status_code=201)
async def create_account(request: Request) -> JSONResponse:
    """Create a new account for an agent."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    verified = await verify_jws_token(data["token"])
    caller_agent_id = verified["agent_id"]
    is_platform = caller_agent_id == get_platform_agent_id()

    payload = verified["payload"]
    action = payload.get("action")
    if action != "create_account":
        raise ServiceError("INVALID_PAYLOAD", "Invalid action in JWS payload", 400, {})

    agent_id = payload.get("agent_id")
    if not agent_id or not isinstance(agent_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing agent_id in JWS payload", 400, {})

    # Non-platform callers can only create their own account
    if not is_platform and agent_id != caller_agent_id:
        raise ServiceError(
            "FORBIDDEN",
            "Agents can only create their own account",
            403,
            {},
        )

    initial_balance = payload.get("initial_balance")
    if initial_balance is None:
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Missing initial_balance in JWS payload",
            400,
            {},
        )
    if not isinstance(initial_balance, int) or initial_balance < 0:
        raise ServiceError(
            "INVALID_AMOUNT",
            "initial_balance must be a non-negative integer",
            400,
            {},
        )

    # Non-platform callers must use initial_balance of 0
    if not is_platform and initial_balance != 0:
        raise ServiceError(
            "FORBIDDEN",
            "Only the platform can set a non-zero initial balance",
            403,
            {},
        )

    # Verify agent exists in Identity service
    if state.identity_client is None:
        msg = "Identity client not initialized"
        raise RuntimeError(msg)
    agent = await state.identity_client.get_agent(agent_id)
    if agent is None:
        raise ServiceError(
            "AGENT_NOT_FOUND",
            "Agent does not exist in Identity service",
            404,
            {},
        )

    result = await run_in_threadpool(state.ledger.create_account, agent_id, initial_balance)
    get_logger(__name__).info(
        "Account created",
        extra={"account_id": agent_id, "initial_balance": initial_balance},
    )
    return JSONResponse(status_code=201, content=result)


# === POST /accounts/{account_id}/credit — Add Funds (Platform-only) ===


@router.post("/accounts/{account_id}/credit")
async def credit_account(request: Request, account_id: str) -> dict[str, object]:
    """Add funds to an account. Platform-only."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    verified = await verify_jws_token(data["token"])
    require_platform(verified["agent_id"], get_platform_agent_id())

    payload = verified["payload"]
    action = payload.get("action")
    if action != "credit":
        raise ServiceError("INVALID_PAYLOAD", "Invalid action in JWS payload", 400, {})

    payload_account_id = payload.get("account_id")
    if payload_account_id is not None and payload_account_id != account_id:
        raise ServiceError(
            "PAYLOAD_MISMATCH",
            "JWS payload account_id does not match URL",
            400,
            {},
        )

    amount = payload.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        raise ServiceError("INVALID_AMOUNT", "Amount must be a positive integer", 400, {})

    reference = payload.get("reference")
    if reference is None:
        raise ServiceError(
            "INVALID_PAYLOAD",
            "Missing reference in JWS payload",
            400,
            {},
        )
    if not isinstance(reference, str):
        raise ServiceError("INVALID_PAYLOAD", "reference must be a string", 400, {})

    result = await run_in_threadpool(state.ledger.credit, account_id, amount, reference)
    get_logger(__name__).info(
        "Account credited",
        extra={
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
            "tx_id": result.get("tx_id"),
        },
    )
    return result


# === GET /accounts/{account_id} — Check Balance (Agent, own account) ===


@router.get("/accounts/{account_id}")
async def get_balance(request: Request, account_id: str) -> dict[str, object]:
    """Check account balance. Agent can only view own account."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ServiceError(
            "INVALID_JWS",
            "Missing Bearer token in Authorization header",
            400,
            {},
        )

    token = auth_header[7:]  # Strip "Bearer "

    verified = await verify_jws_token(token)
    require_account_owner(verified["agent_id"], account_id)
    payload = verified["payload"]

    action = payload.get("action")
    if action != "get_balance":
        raise ServiceError("INVALID_PAYLOAD", "Invalid action in JWS payload", 400, {})

    payload_account_id = payload.get("account_id")
    if payload_account_id is not None and payload_account_id != account_id:
        raise ServiceError(
            "PAYLOAD_MISMATCH",
            "JWS payload account_id does not match URL",
            400,
            {},
        )

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    account = await run_in_threadpool(state.ledger.get_account, account_id)
    if account is None:
        raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

    return account


# === GET /accounts/{account_id}/transactions — Transaction History (Agent, own account) ===


@router.get("/accounts/{account_id}/transactions")
async def get_transactions(request: Request, account_id: str) -> dict[str, list[dict[str, object]]]:
    """Get transaction history. Agent can only view own account."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ServiceError(
            "INVALID_JWS",
            "Missing Bearer token in Authorization header",
            400,
            {},
        )

    token = auth_header[7:]

    verified = await verify_jws_token(token)
    require_account_owner(verified["agent_id"], account_id)
    payload = verified["payload"]

    action = payload.get("action")
    if action != "get_transactions":
        raise ServiceError("INVALID_PAYLOAD", "Invalid action in JWS payload", 400, {})

    payload_account_id = payload.get("account_id")
    if payload_account_id is not None and payload_account_id != account_id:
        raise ServiceError(
            "PAYLOAD_MISMATCH",
            "JWS payload account_id does not match URL",
            400,
            {},
        )

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    transactions = await run_in_threadpool(state.ledger.get_transactions, account_id)
    return {"transactions": transactions}


# === Method-not-allowed handlers ===


@router.api_route(
    "/accounts",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def accounts_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /accounts."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
