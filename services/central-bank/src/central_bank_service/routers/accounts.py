"""Account endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_auth.financial_responses import (
    FinancialLeg,
    NativeFinancialEnvelope,
    validate_operation_id,
)
from service_commons.exceptions import ServiceError

from central_bank_service.core.state import get_app_state
from central_bank_service.logging import get_logger
from central_bank_service.monetary import require_nonnegative_money, require_positive_money
from central_bank_service.routers.helpers import (
    get_platform_agent_id,
    parse_json_body,
    require_account_owner,
    verify_jws_token,
    verify_platform_signature,
)

router = APIRouter()


def _native_financial_result(
    value: dict[str, object],
    expected_kind: str,
    expected_role: str,
) -> tuple[NativeFinancialEnvelope, FinancialLeg]:
    envelope = NativeFinancialEnvelope.model_validate(value)
    receipt = envelope.receipt
    if receipt.operation_kind != expected_kind or len(receipt.legs) != 1:
        raise RuntimeError("Ledger returned an unexpected native financial receipt")
    leg = receipt.legs[0]
    if leg.role != expected_role:
        raise RuntimeError("Ledger returned an unexpected native financial leg")
    return envelope, leg


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


# === POST /accounts — Create Account ===


@router.post("/accounts", status_code=201)
async def create_account(request: Request) -> JSONResponse:
    """Create a new account for an agent."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("invalid_jws", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("invalid_jws", "JWS token must be a string", 400, {})

    state = get_app_state()
    if state.ledger is None:
        raise ServiceError(
            error="service_not_ready",
            message="Ledger not initialized",
            status_code=503,
            details={},
        )

    verified = await verify_jws_token(
        data["token"],
        audience="central-bank",
        resource="POST /accounts",
        allowed_action="create_account",
    )
    caller_agent_id = verified.header.kid
    is_platform = caller_agent_id == get_platform_agent_id()

    payload = verified.payload
    _reject_unknown_payload_fields(
        payload,
        {"action", "agent_id", "initial_balance", "operation_id"},
    )

    agent_id = payload.get("agent_id")
    if not agent_id or not isinstance(agent_id, str):
        raise ServiceError("invalid_payload", "Missing agent_id in JWS payload", 400, {})

    # Non-platform callers can only create their own account
    if not is_platform and agent_id != caller_agent_id:
        raise ServiceError(
            "forbidden",
            "Agents can only create their own account",
            403,
            {},
        )

    raw_initial_balance = payload.get("initial_balance")
    if raw_initial_balance is None:
        raise ServiceError(
            "invalid_payload",
            "Missing initial_balance in JWS payload",
            400,
            {},
        )
    initial_balance = require_nonnegative_money(raw_initial_balance, "initial_balance")

    # Non-platform callers must use initial_balance of 0
    if not is_platform and initial_balance != 0:
        raise ServiceError(
            "forbidden",
            "Only the platform can set a non-zero initial balance",
            403,
            {},
        )

    # Verify agent exists in Identity service
    if state.identity_client is None:
        raise ServiceError(
            error="service_not_ready",
            message="Identity client not initialized",
            status_code=503,
            details={},
        )
    agent = await state.identity_client.get_agent(agent_id)
    if agent is None:
        raise ServiceError(
            "agent_not_found",
            "Agent does not exist in Identity service",
            404,
            {},
        )

    operation_id: str | None = None
    if initial_balance > 0:
        operation_id = _required_operation_id(payload)
    elif "operation_id" in payload:
        raise ServiceError(
            "invalid_payload",
            "operation_id is not allowed for zero-balance account creation",
            400,
            {},
        )
    result = await state.ledger.create_account(agent_id, initial_balance, operation_id)

    log_context: dict[str, object] = {
        "account_id": agent_id,
        "initial_balance": initial_balance,
    }
    if operation_id is not None:
        envelope, leg = _native_financial_result(result, "account_issuance", "account")
        log_context = {
            "account_id": leg.account_id,
            "initial_balance": leg.amount,
            "operation_id": envelope.receipt.operation_id,
        }
    get_logger(__name__).info(
        "Account created",
        extra=log_context,
    )
    return JSONResponse(status_code=201, content=result)


# === POST /accounts/{account_id}/credit — Add Funds (Platform-only) ===


@router.post("/accounts/{account_id}/credit")
async def credit_account(request: Request, account_id: str) -> dict[str, object]:
    """Add funds to an account. Platform-signed, verified locally."""
    body = await request.body()
    data = parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("invalid_jws", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("invalid_jws", "JWS token must be a string", 400, {})

    state = get_app_state()
    if state.ledger is None:
        raise ServiceError(
            error="service_not_ready",
            message="Ledger not initialized",
            status_code=503,
            details={},
        )

    verified = verify_platform_signature(
        data["token"],
        audience="central-bank",
        resource=f"POST /accounts/{account_id}/credit",
        allowed_action="credit",
    )
    payload = verified.payload
    _reject_unknown_payload_fields(
        payload,
        {"action", "account_id", "amount", "reference", "operation_id"},
    )

    payload_account_id = payload.get("account_id")
    if payload_account_id is not None and payload_account_id != account_id:
        raise ServiceError(
            "payload_mismatch",
            "JWS payload account_id does not match URL",
            400,
            {},
        )

    amount = require_positive_money(payload.get("amount"), "amount")

    reference = payload.get("reference")
    if reference is None:
        raise ServiceError(
            "invalid_payload",
            "Missing reference in JWS payload",
            400,
            {},
        )
    if not isinstance(reference, str):
        raise ServiceError("invalid_payload", "reference must be a string", 400, {})
    operation_id = _required_operation_id(payload)

    result = await state.ledger.credit(account_id, amount, reference, operation_id)

    envelope, leg = _native_financial_result(result, "credit", "account")
    get_logger(__name__).info(
        "Account credited",
        extra={
            "account_id": leg.account_id,
            "amount": leg.amount,
            "reference": envelope.receipt.source_reference,
            "operation_id": envelope.receipt.operation_id,
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
            "invalid_jws",
            "Missing Bearer token in Authorization header",
            400,
            {},
        )

    token = auth_header[7:]  # Strip "Bearer "

    verified = await verify_jws_token(
        token,
        audience="central-bank",
        resource=f"GET /accounts/{account_id}",
        allowed_action="get_balance",
    )
    require_account_owner(verified.header.kid, account_id)
    payload = verified.payload

    payload_account_id = payload.get("account_id")
    if payload_account_id is not None and payload_account_id != account_id:
        raise ServiceError(
            "payload_mismatch",
            "JWS payload account_id does not match URL",
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

    account = await state.ledger.get_account(account_id)
    if account is None:
        raise ServiceError("account_not_found", "Account not found", 404, {})

    return account


# === GET /accounts/{account_id}/transactions — Transaction History (Agent, own account) ===


@router.get("/accounts/{account_id}/transactions")
async def get_transactions(request: Request, account_id: str) -> dict[str, list[dict[str, object]]]:
    """Get transaction history. Agent can only view own account."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ServiceError(
            "invalid_jws",
            "Missing Bearer token in Authorization header",
            400,
            {},
        )

    token = auth_header[7:]

    verified = await verify_jws_token(
        token,
        audience="central-bank",
        resource=f"GET /accounts/{account_id}/transactions",
        allowed_action="get_transactions",
    )
    require_account_owner(verified.header.kid, account_id)
    payload = verified.payload

    payload_account_id = payload.get("account_id")
    if payload_account_id is not None and payload_account_id != account_id:
        raise ServiceError(
            "payload_mismatch",
            "JWS payload account_id does not match URL",
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

    transactions = await state.ledger.get_transactions(account_id)
    return {"transactions": transactions}


# === Method-not-allowed handlers ===


@router.api_route(
    "/accounts",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def accounts_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /accounts."""
    raise ServiceError("method_not_allowed", "Method not allowed", 405, {})
