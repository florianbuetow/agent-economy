# Phase 7 — Routers

## Working Directory

All paths relative to `services/central-bank/`.

---

## Task B7: Implement routers (health, accounts, escrow)

### Step 7.1: Write routers/health.py

Create `services/central-bank/src/central_bank_service/routers/health.py`:

```python
"""Health check endpoint."""

from __future__ import annotations

from fastapi import APIRouter

from central_bank_service.core.state import get_app_state
from central_bank_service.schemas import HealthResponse

router = APIRouter()


@router.get("/health", response_model=HealthResponse)
async def health_check() -> HealthResponse:
    """Check service health and return statistics."""
    state = get_app_state()
    total_accounts = 0
    total_escrowed = 0
    if state.ledger is not None:
        total_accounts = state.ledger.count_accounts()
        total_escrowed = state.ledger.total_escrowed()
    return HealthResponse(
        status="ok",
        uptime_seconds=state.uptime_seconds,
        started_at=state.started_at,
        total_accounts=total_accounts,
        total_escrowed=total_escrowed,
    )
```

### Step 7.2: Write routers/accounts.py

Create `services/central-bank/src/central_bank_service/routers/accounts.py`:

```python
"""Account endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from central_bank_service.core.state import get_app_state

router = APIRouter()


def _parse_json_body(body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JSON",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "INVALID_JSON",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


async def _verify_jws_token(token: str) -> dict[str, Any]:
    """Verify a JWS token via the Identity service. Returns the verified response."""
    state = get_app_state()
    if state.identity_client is None:
        msg = "Identity client not initialized"
        raise RuntimeError(msg)

    result = await state.identity_client.verify_jws(token)

    if not result.get("valid"):
        raise ServiceError(
            "FORBIDDEN",
            "JWS signature verification failed",
            403,
            {},
        )

    return result


def _require_platform(agent_id: str, platform_agent_id: str) -> None:
    """Check that the verified agent is the platform."""
    if agent_id != platform_agent_id:
        raise ServiceError(
            "FORBIDDEN",
            "Only the platform agent can perform this operation",
            403,
            {},
        )


def _require_account_owner(verified_agent_id: str, account_id: str) -> None:
    """Check that the verified agent owns the account."""
    if verified_agent_id != account_id:
        raise ServiceError(
            "FORBIDDEN",
            "You can only access your own account",
            403,
            {},
        )


# === POST /accounts — Create Account (Platform-only) ===

@router.post("/accounts", status_code=201)
async def create_account(request: Request) -> JSONResponse:
    """Create a new account for an agent. Platform-only."""
    body = await request.body()
    data = _parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    from central_bank_service.config import get_settings  # noqa: PLC0415

    settings = get_settings()

    verified = await _verify_jws_token(data["token"])
    _require_platform(verified["agent_id"], settings.platform.agent_id)

    payload = verified["payload"]
    agent_id = payload.get("agent_id")
    if not agent_id or not isinstance(agent_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing agent_id in JWS payload", 400, {})

    initial_balance = payload.get("initial_balance", 0)
    if not isinstance(initial_balance, int) or initial_balance < 0:
        raise ServiceError("INVALID_AMOUNT", "initial_balance must be a non-negative integer", 400, {})

    # Verify agent exists in Identity service
    if state.identity_client is None:
        msg = "Identity client not initialized"
        raise RuntimeError(msg)
    agent = await state.identity_client.get_agent(agent_id)
    if agent is None:
        raise ServiceError("AGENT_NOT_FOUND", "Agent does not exist in Identity service", 404, {})

    result = state.ledger.create_account(agent_id, initial_balance)
    return JSONResponse(status_code=201, content=result)


# === POST /accounts/{account_id}/credit — Add Funds (Platform-only) ===

@router.post("/accounts/{account_id}/credit")
async def credit_account(request: Request, account_id: str) -> dict[str, object]:
    """Add funds to an account. Platform-only."""
    body = await request.body()
    data = _parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    from central_bank_service.config import get_settings  # noqa: PLC0415

    settings = get_settings()

    verified = await _verify_jws_token(data["token"])
    _require_platform(verified["agent_id"], settings.platform.agent_id)

    payload = verified["payload"]
    amount = payload.get("amount")
    if not isinstance(amount, int) or amount <= 0:
        raise ServiceError("INVALID_AMOUNT", "Amount must be a positive integer", 400, {})

    reference = payload.get("reference", "")
    if not isinstance(reference, str):
        raise ServiceError("INVALID_PAYLOAD", "reference must be a string", 400, {})

    return state.ledger.credit(account_id, amount, reference)


# === GET /accounts/{account_id} — Check Balance (Agent, own account) ===

@router.get("/accounts/{account_id}")
async def get_balance(request: Request, account_id: str) -> dict[str, object]:
    """Check account balance. Agent can only view own account."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ServiceError("INVALID_JWS", "Missing Bearer token in Authorization header", 400, {})

    token = auth_header[7:]  # Strip "Bearer "

    verified = await _verify_jws_token(token)
    _require_account_owner(verified["agent_id"], account_id)

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    account = state.ledger.get_account(account_id)
    if account is None:
        raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

    return account


# === GET /accounts/{account_id}/transactions — Transaction History (Agent, own account) ===

@router.get("/accounts/{account_id}/transactions")
async def get_transactions(request: Request, account_id: str) -> dict[str, list[dict[str, object]]]:
    """Get transaction history. Agent can only view own account."""
    auth_header = request.headers.get("authorization", "")
    if not auth_header.startswith("Bearer "):
        raise ServiceError("INVALID_JWS", "Missing Bearer token in Authorization header", 400, {})

    token = auth_header[7:]

    verified = await _verify_jws_token(token)
    _require_account_owner(verified["agent_id"], account_id)

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    transactions = state.ledger.get_transactions(account_id)
    return {"transactions": transactions}


# === Method-not-allowed handlers ===

@router.api_route(
    "/accounts",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def accounts_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /accounts."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
```

### Step 7.3: Write routers/escrow.py

Create `services/central-bank/src/central_bank_service/routers/escrow.py`:

```python
"""Escrow endpoints."""

from __future__ import annotations

import json
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse
from service_commons.exceptions import ServiceError

from central_bank_service.core.state import get_app_state

router = APIRouter()


def _parse_json_body(body: bytes) -> dict[str, Any]:
    """Parse JSON body, raising ServiceError on failure."""
    try:
        data = json.loads(body)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise ServiceError(
            "INVALID_JSON",
            "Request body is not valid JSON",
            400,
            {},
        ) from exc

    if not isinstance(data, dict):
        raise ServiceError(
            "INVALID_JSON",
            "Request body must be a JSON object",
            400,
            {},
        )

    return data


async def _verify_jws_token(token: str) -> dict[str, Any]:
    """Verify a JWS token via the Identity service."""
    state = get_app_state()
    if state.identity_client is None:
        msg = "Identity client not initialized"
        raise RuntimeError(msg)

    result = await state.identity_client.verify_jws(token)

    if not result.get("valid"):
        raise ServiceError(
            "FORBIDDEN",
            "JWS signature verification failed",
            403,
            {},
        )

    return result


def _require_platform(agent_id: str, platform_agent_id: str) -> None:
    """Check that the verified agent is the platform."""
    if agent_id != platform_agent_id:
        raise ServiceError(
            "FORBIDDEN",
            "Only the platform agent can perform this operation",
            403,
            {},
        )


# === POST /escrow/lock — Lock Funds in Escrow (Agent-signed) ===

@router.post("/escrow/lock", status_code=201)
async def escrow_lock(request: Request) -> JSONResponse:
    """Lock funds in escrow. Requires agent's own JWS signature."""
    body = await request.body()
    data = _parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    verified = await _verify_jws_token(data["token"])
    payload = verified["payload"]

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

    result = state.ledger.escrow_lock(agent_id, amount, task_id)
    return JSONResponse(status_code=201, content=result)


# === POST /escrow/{escrow_id}/release — Full Payout (Platform-signed) ===

@router.post("/escrow/{escrow_id}/release")
async def escrow_release(request: Request, escrow_id: str) -> dict[str, object]:
    """Release escrowed funds to recipient. Platform-only."""
    body = await request.body()
    data = _parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    from central_bank_service.config import get_settings  # noqa: PLC0415

    settings = get_settings()

    verified = await _verify_jws_token(data["token"])
    _require_platform(verified["agent_id"], settings.platform.agent_id)

    payload = verified["payload"]
    recipient_account_id = payload.get("recipient_account_id")
    if not recipient_account_id or not isinstance(recipient_account_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing recipient_account_id in JWS payload", 400, {})

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    return state.ledger.escrow_release(escrow_id, recipient_account_id)


# === POST /escrow/{escrow_id}/split — Proportional Split (Platform-signed) ===

@router.post("/escrow/{escrow_id}/split")
async def escrow_split(request: Request, escrow_id: str) -> dict[str, object]:
    """Split escrowed funds between worker and poster. Platform-only."""
    body = await request.body()
    data = _parse_json_body(body)

    if "token" not in data or data["token"] is None:
        raise ServiceError("INVALID_JWS", "Missing JWS token in request body", 400, {})
    if not isinstance(data["token"], str):
        raise ServiceError("INVALID_JWS", "JWS token must be a string", 400, {})

    from central_bank_service.config import get_settings  # noqa: PLC0415

    settings = get_settings()

    verified = await _verify_jws_token(data["token"])
    _require_platform(verified["agent_id"], settings.platform.agent_id)

    payload = verified["payload"]

    worker_account_id = payload.get("worker_account_id")
    if not worker_account_id or not isinstance(worker_account_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing worker_account_id in JWS payload", 400, {})

    poster_account_id = payload.get("poster_account_id")
    if not poster_account_id or not isinstance(poster_account_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing poster_account_id in JWS payload", 400, {})

    worker_pct = payload.get("worker_pct")
    if not isinstance(worker_pct, int):
        raise ServiceError("INVALID_PAYLOAD", "worker_pct must be an integer", 400, {})

    state = get_app_state()
    if state.ledger is None:
        msg = "Ledger not initialized"
        raise RuntimeError(msg)

    return state.ledger.escrow_split(escrow_id, worker_account_id, worker_pct, poster_account_id)


# === Method-not-allowed handlers ===

@router.api_route(
    "/escrow/lock",
    methods=["GET", "PUT", "PATCH", "DELETE"],
)
async def escrow_lock_method_not_allowed(_request: Request) -> None:
    """Reject wrong methods on /escrow/lock."""
    raise ServiceError("METHOD_NOT_ALLOWED", "Method not allowed", 405, {})
```

### Step 7.4: Write routers/__init__.py

Replace the empty `services/central-bank/src/central_bank_service/routers/__init__.py` with:

```python
"""API routers."""

from central_bank_service.routers import accounts, escrow, health

__all__ = ["accounts", "escrow", "health"]
```

### Step 7.5: Commit

```bash
git add services/central-bank/src/central_bank_service/routers/
git commit -m "feat(central-bank): add routers (health, accounts, escrow)"
```

---

## Verification

```bash
cd services/central-bank && uv run ruff check src/ && uv run ruff format --check src/
```
