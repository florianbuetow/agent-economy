# Gateway Store Implementation Plan — All Services

**Date:** 2026-03-02
**Tickets:** agent-economy-e2uh, agent-economy-1drj, agent-economy-8s03, agent-economy-lsno, agent-economy-nww7
**Scope:** Bug fix + 4 Gateway*Store implementations across 5 services

## Overview

This plan implements Gateway*Store classes for Central Bank, Reputation, Task Board, and Court services, plus a small bug fix for db-gateway integration tests. Each Gateway*Store replaces the local SQLite store with an HTTP client that reads/writes through the DB Gateway API.

**IMPORTANT: Do NOT use git anywhere. There is no git in this project. Skip all git commands.**

## Pre-requisites (already complete)

All 4 P1 dependencies are closed:
- agent-economy-zpby: Schema divergences fixed
- agent-economy-a3gy: GET read endpoints added (only identity GET endpoints exist — bank/board/reputation/court GET endpoints need to be added as part of this work)
- agent-economy-2g1b: Constraint support added to DB Gateway write endpoints
- agent-economy-523w: Store Protocol interfaces defined for all services

**CRITICAL DISCOVERY:** The DB Gateway currently only has GET (read) endpoints for Identity (agents). It has NO GET endpoints for bank, board, reputation, or court tables. The DbReader class only has identity methods. Therefore, this plan MUST add GET endpoints to the DB Gateway before the Gateway*Store implementations can work.

## Architecture

```
Service (e.g., Central Bank)
  └── GatewayLedgerStore (implements LedgerStorageInterface)
        └── httpx.AsyncClient → DB Gateway HTTP API
              ├── POST endpoints (already exist for writes)
              └── GET endpoints (MUST BE ADDED for reads)
```

The reference implementation is `services/identity/src/identity_service/services/gateway_agent_store.py` — this is the only service that already has a working Gateway*Store.

---

## Tier 0: Bug Fix (agent-economy-e2uh)

**File:** `services/db-gateway/tests/integration/conftest.py`
**Change:** Fix port from 8006 to 8007

```python
# BEFORE:
@pytest.fixture
def gateway_url() -> str:
    return "http://localhost:8006"

# AFTER:
@pytest.fixture
def gateway_url() -> str:
    return "http://localhost:8007"
```

**File:** `services/db-gateway/tests/integration/test_endpoints.py`
**Change:** The health assertions are actually correct — the health endpoint returns `database_size_bytes` and `total_events`. Verify by reading `services/db-gateway/src/db_gateway_service/schemas.py` and `routers/health.py`.

**Verification:**
```bash
cd services/db-gateway && just ci-quiet
```

---

## Tier 1: Add GET Endpoints to DB Gateway

The DB Gateway currently only has GET endpoints for identity. We need to add GET endpoints for bank, board, reputation, and court so the Gateway*Store implementations can read data.

### Tier 1A: Extend DbReader (services/db-gateway/src/db_gateway_service/services/db_reader.py)

Add the following methods to the existing `DbReader` class. Follow the exact same pattern as the identity methods.

**Bank Methods:**

```python
def get_account(self, account_id: str) -> dict[str, Any] | None:
    """Get a bank account by ID."""
    cursor = self._db.execute(
        "SELECT account_id, balance, created_at FROM bank_accounts WHERE account_id = ?",
        (account_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {"account_id": row[0], "balance": row[1], "created_at": row[2]}

def get_transactions(self, account_id: str) -> list[dict[str, Any]]:
    """Get transaction history for an account."""
    cursor = self._db.execute(
        "SELECT tx_id, account_id, type, amount, balance_after, reference, timestamp "
        "FROM bank_transactions WHERE account_id = ? ORDER BY timestamp, tx_id",
        (account_id,),
    )
    return [
        {
            "tx_id": row[0], "account_id": row[1], "type": row[2],
            "amount": row[3], "balance_after": row[4], "reference": row[5],
            "timestamp": row[6],
        }
        for row in cursor.fetchall()
    ]

def count_accounts(self) -> int:
    """Count total bank accounts."""
    cursor = self._db.execute("SELECT COUNT(*) FROM bank_accounts")
    row = cursor.fetchone()
    return int(row[0]) if row else 0

def total_escrowed(self) -> int:
    """Sum of all locked escrow amounts."""
    cursor = self._db.execute(
        "SELECT COALESCE(SUM(amount), 0) FROM bank_escrow WHERE status = 'locked'"
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0

def get_escrow(self, escrow_id: str) -> dict[str, Any] | None:
    """Get an escrow record by ID."""
    cursor = self._db.execute(
        "SELECT escrow_id, payer_account_id, amount, task_id, status, "
        "created_at, resolved_at FROM bank_escrow WHERE escrow_id = ?",
        (escrow_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "escrow_id": row[0], "payer_account_id": row[1], "amount": row[2],
        "task_id": row[3], "status": row[4], "created_at": row[5],
        "resolved_at": row[6],
    }
```

**Board Methods:**

```python
def get_task(self, task_id: str) -> dict[str, Any] | None:
    """Get a task by ID."""
    cursor = self._db.execute(
        "SELECT * FROM board_tasks WHERE task_id = ?",
        (task_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return dict(row) if hasattr(row, 'keys') else self._row_to_dict_board_task(row)

def list_tasks(
    self,
    status: str | None,
    poster_id: str | None,
    worker_id: str | None,
    limit: int | None,
    offset: int | None,
) -> list[dict[str, Any]]:
    """List tasks with optional filters."""
    query = "SELECT * FROM board_tasks"
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if poster_id is not None:
        clauses.append("poster_id = ?")
        params.append(poster_id)
    if worker_id is not None:
        clauses.append("worker_id = ?")
        params.append(worker_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY created_at DESC"
    if limit is not None:
        query += " LIMIT ?"
        params.append(limit)
    if offset is not None:
        query += " OFFSET ?"
        params.append(offset)
    rows = self._db.execute(query, params).fetchall()
    return [dict(row) for row in rows]

def count_tasks(self) -> int:
    """Count total tasks."""
    cursor = self._db.execute("SELECT COUNT(*) FROM board_tasks")
    row = cursor.fetchone()
    return int(row[0]) if row else 0

def count_tasks_by_status(self) -> dict[str, int]:
    """Count tasks grouped by status."""
    rows = self._db.execute(
        "SELECT status, COUNT(*) FROM board_tasks GROUP BY status"
    ).fetchall()
    return {str(row[0]): int(row[1]) for row in rows}

def get_bid(self, bid_id: str, task_id: str) -> dict[str, Any] | None:
    """Get a bid by ID and task_id."""
    cursor = self._db.execute(
        "SELECT bid_id, task_id, bidder_id, proposal, amount, submitted_at "
        "FROM board_bids WHERE bid_id = ? AND task_id = ?",
        (bid_id, task_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "bid_id": row[0], "task_id": row[1], "bidder_id": row[2],
        "proposal": row[3], "amount": row[4], "submitted_at": row[5],
    }

def get_bids_for_task(self, task_id: str) -> list[dict[str, Any]]:
    """Get all bids for a task."""
    cursor = self._db.execute(
        "SELECT bid_id, task_id, bidder_id, proposal, amount, submitted_at "
        "FROM board_bids WHERE task_id = ? ORDER BY submitted_at",
        (task_id,),
    )
    return [
        {
            "bid_id": row[0], "task_id": row[1], "bidder_id": row[2],
            "proposal": row[3], "amount": row[4], "submitted_at": row[5],
        }
        for row in cursor.fetchall()
    ]

def get_asset(self, asset_id: str, task_id: str) -> dict[str, Any] | None:
    """Get an asset by ID and task_id."""
    cursor = self._db.execute(
        "SELECT asset_id, task_id, uploader_id, filename, content_type, "
        "size_bytes, storage_path, content_hash, uploaded_at "
        "FROM board_assets WHERE asset_id = ? AND task_id = ?",
        (asset_id, task_id),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "asset_id": row[0], "task_id": row[1], "uploader_id": row[2],
        "filename": row[3], "content_type": row[4], "size_bytes": row[5],
        "storage_path": row[6], "content_hash": row[7], "uploaded_at": row[8],
    }

def get_assets_for_task(self, task_id: str) -> list[dict[str, Any]]:
    """Get all assets for a task."""
    cursor = self._db.execute(
        "SELECT asset_id, task_id, uploader_id, filename, content_type, "
        "size_bytes, storage_path, content_hash, uploaded_at "
        "FROM board_assets WHERE task_id = ? ORDER BY uploaded_at",
        (task_id,),
    )
    return [
        {
            "asset_id": row[0], "task_id": row[1], "uploader_id": row[2],
            "filename": row[3], "content_type": row[4], "size_bytes": row[5],
            "storage_path": row[6], "content_hash": row[7], "uploaded_at": row[8],
        }
        for row in cursor.fetchall()
    ]

def count_assets(self, task_id: str) -> int:
    """Count assets for a task."""
    cursor = self._db.execute(
        "SELECT COUNT(*) FROM board_assets WHERE task_id = ?", (task_id,)
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0
```

**Reputation Methods:**

```python
def get_feedback(self, feedback_id: str) -> dict[str, Any] | None:
    """Get feedback by ID."""
    cursor = self._db.execute(
        "SELECT feedback_id, task_id, from_agent_id, to_agent_id, role, "
        "category, rating, comment, submitted_at, visible "
        "FROM reputation_feedback WHERE feedback_id = ?",
        (feedback_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "feedback_id": row[0], "task_id": row[1], "from_agent_id": row[2],
        "to_agent_id": row[3], "role": row[4], "category": row[5],
        "rating": row[6], "comment": row[7], "submitted_at": row[8],
        "visible": bool(row[9]),
    }

def get_feedback_by_task(self, task_id: str) -> list[dict[str, Any]]:
    """Get all feedback for a task."""
    cursor = self._db.execute(
        "SELECT feedback_id, task_id, from_agent_id, to_agent_id, role, "
        "category, rating, comment, submitted_at, visible "
        "FROM reputation_feedback WHERE task_id = ? ORDER BY submitted_at",
        (task_id,),
    )
    return [self._feedback_row_to_dict(row) for row in cursor.fetchall()]

def get_feedback_by_agent(self, agent_id: str) -> list[dict[str, Any]]:
    """Get all feedback where to_agent_id matches."""
    cursor = self._db.execute(
        "SELECT feedback_id, task_id, from_agent_id, to_agent_id, role, "
        "category, rating, comment, submitted_at, visible "
        "FROM reputation_feedback WHERE to_agent_id = ? ORDER BY submitted_at",
        (agent_id,),
    )
    return [self._feedback_row_to_dict(row) for row in cursor.fetchall()]

def count_feedback(self) -> int:
    """Count total feedback records."""
    cursor = self._db.execute("SELECT COUNT(*) FROM reputation_feedback")
    row = cursor.fetchone()
    return int(row[0]) if row else 0

def _feedback_row_to_dict(self, row: tuple[object, ...]) -> dict[str, Any]:
    return {
        "feedback_id": row[0], "task_id": row[1], "from_agent_id": row[2],
        "to_agent_id": row[3], "role": row[4], "category": row[5],
        "rating": row[6], "comment": row[7], "submitted_at": row[8],
        "visible": bool(row[9]),
    }
```

**Court Methods:**

```python
def get_claim(self, claim_id: str) -> dict[str, Any] | None:
    """Get a court claim by ID."""
    cursor = self._db.execute(
        "SELECT claim_id, task_id, claimant_id, respondent_id, reason, "
        "status, rebuttal_deadline, filed_at "
        "FROM court_claims WHERE claim_id = ?",
        (claim_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "claim_id": row[0], "task_id": row[1], "claimant_id": row[2],
        "respondent_id": row[3], "reason": row[4], "status": row[5],
        "rebuttal_deadline": row[6], "filed_at": row[7],
    }

def list_claims(
    self, status: str | None, claimant_id: str | None,
) -> list[dict[str, Any]]:
    """List claims with optional filters."""
    query = (
        "SELECT claim_id, task_id, claimant_id, respondent_id, reason, "
        "status, rebuttal_deadline, filed_at FROM court_claims"
    )
    clauses: list[str] = []
    params: list[object] = []
    if status is not None:
        clauses.append("status = ?")
        params.append(status)
    if claimant_id is not None:
        clauses.append("claimant_id = ?")
        params.append(claimant_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY filed_at"
    return [
        {
            "claim_id": row[0], "task_id": row[1], "claimant_id": row[2],
            "respondent_id": row[3], "reason": row[4], "status": row[5],
            "rebuttal_deadline": row[6], "filed_at": row[7],
        }
        for row in self._db.execute(query, params).fetchall()
    ]

def get_rebuttal(self, claim_id: str) -> dict[str, Any] | None:
    """Get rebuttal for a claim."""
    cursor = self._db.execute(
        "SELECT rebuttal_id, claim_id, agent_id, content, submitted_at "
        "FROM court_rebuttals WHERE claim_id = ?",
        (claim_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "rebuttal_id": row[0], "claim_id": row[1], "agent_id": row[2],
        "content": row[3], "submitted_at": row[4],
    }

def get_ruling(self, claim_id: str) -> dict[str, Any] | None:
    """Get ruling for a claim."""
    cursor = self._db.execute(
        "SELECT ruling_id, claim_id, task_id, worker_pct, summary, "
        "judge_votes, ruled_at FROM court_rulings WHERE claim_id = ?",
        (claim_id,),
    )
    row = cursor.fetchone()
    if row is None:
        return None
    return {
        "ruling_id": row[0], "claim_id": row[1], "task_id": row[2],
        "worker_pct": row[3], "summary": row[4],
        "judge_votes": row[5], "ruled_at": row[6],
    }

def count_claims(self) -> int:
    """Count total claims."""
    cursor = self._db.execute("SELECT COUNT(*) FROM court_claims")
    row = cursor.fetchone()
    return int(row[0]) if row else 0

def count_active_claims(self) -> int:
    """Count claims not yet ruled."""
    cursor = self._db.execute(
        "SELECT COUNT(*) FROM court_claims WHERE status != 'ruled'"
    )
    row = cursor.fetchone()
    return int(row[0]) if row else 0
```

### Tier 1B: Add GET Endpoint Routes to DB Gateway

Add GET routes to the existing router files. Follow the same pattern as `routers/identity.py`.

**File: `services/db-gateway/src/db_gateway_service/routers/bank.py`**

Add these GET endpoints AFTER the existing POST endpoints:

```python
@router.get("/accounts/{account_id}")
async def get_account(account_id: str) -> JSONResponse:
    """Get a bank account by ID."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    account = state.db_reader.get_account(account_id)
    if account is None:
        raise ServiceError(error="account_not_found", message="Account not found", status_code=404, details={})
    return JSONResponse(status_code=200, content=account)

@router.get("/accounts/{account_id}/transactions")
async def get_transactions(account_id: str) -> JSONResponse:
    """Get transaction history for an account."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    txns = state.db_reader.get_transactions(account_id)
    return JSONResponse(status_code=200, content={"transactions": txns})

@router.get("/accounts/count")
async def count_accounts() -> JSONResponse:
    """Count total bank accounts."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    count = state.db_reader.count_accounts()
    return JSONResponse(status_code=200, content={"count": count})

@router.get("/escrow/total-locked")
async def total_escrowed() -> JSONResponse:
    """Get total locked escrow amount."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    total = state.db_reader.total_escrowed()
    return JSONResponse(status_code=200, content={"total": total})

@router.get("/escrow/{escrow_id}")
async def get_escrow(escrow_id: str) -> JSONResponse:
    """Get an escrow record by ID."""
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    escrow = state.db_reader.get_escrow(escrow_id)
    if escrow is None:
        raise ServiceError(error="escrow_not_found", message="Escrow not found", status_code=404, details={})
    return JSONResponse(status_code=200, content=escrow)
```

**IMPORTANT for bank.py route ordering:** The `/accounts/count` GET route MUST be defined BEFORE `/accounts/{account_id}` to avoid FastAPI treating "count" as an account_id parameter. Either reorder or use a separate router prefix.

**File: `services/db-gateway/src/db_gateway_service/routers/board.py`**

Add GET endpoints:

```python
@router.get("/tasks/count")
async def count_tasks() -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    return JSONResponse(status_code=200, content={"count": state.db_reader.count_tasks()})

@router.get("/tasks/count-by-status")
async def count_tasks_by_status() -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    return JSONResponse(status_code=200, content=state.db_reader.count_tasks_by_status())

@router.get("/tasks")
async def list_tasks(request: Request) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    params = request.query_params
    status = params.get("status")
    poster_id = params.get("poster_id")
    worker_id = params.get("worker_id")
    limit_str = params.get("limit")
    offset_str = params.get("offset")
    limit = int(limit_str) if limit_str is not None else None
    offset = int(offset_str) if offset_str is not None else None
    tasks = state.db_reader.list_tasks(status, poster_id, worker_id, limit, offset)
    return JSONResponse(status_code=200, content={"tasks": tasks})

@router.get("/tasks/{task_id}")
async def get_task(task_id: str) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    task = state.db_reader.get_task(task_id)
    if task is None:
        raise ServiceError(error="task_not_found", message="Task not found", status_code=404, details={})
    return JSONResponse(status_code=200, content=task)

@router.get("/tasks/{task_id}/bids")
async def get_bids_for_task(task_id: str) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    bids = state.db_reader.get_bids_for_task(task_id)
    return JSONResponse(status_code=200, content={"bids": bids})

@router.get("/tasks/{task_id}/assets")
async def get_assets_for_task(task_id: str) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    assets = state.db_reader.get_assets_for_task(task_id)
    return JSONResponse(status_code=200, content={"assets": assets})

@router.get("/tasks/{task_id}/assets/count")
async def count_assets(task_id: str) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    count = state.db_reader.count_assets(task_id)
    return JSONResponse(status_code=200, content={"count": count})

@router.get("/bids/{bid_id}")
async def get_bid(bid_id: str, request: Request) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    task_id = request.query_params.get("task_id", "")
    bid = state.db_reader.get_bid(bid_id, task_id)
    if bid is None:
        raise ServiceError(error="bid_not_found", message="Bid not found", status_code=404, details={})
    return JSONResponse(status_code=200, content=bid)

@router.get("/assets/{asset_id}")
async def get_asset(asset_id: str, request: Request) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    task_id = request.query_params.get("task_id", "")
    asset = state.db_reader.get_asset(asset_id, task_id)
    if asset is None:
        raise ServiceError(error="asset_not_found", message="Asset not found", status_code=404, details={})
    return JSONResponse(status_code=200, content=asset)
```

**IMPORTANT for board.py route ordering:** `/tasks/count` and `/tasks/count-by-status` MUST be defined BEFORE `/tasks/{task_id}`. Similarly `/tasks/{task_id}/assets/count` before `/tasks/{task_id}/assets/{asset_id}` if such a route existed.

**File: `services/db-gateway/src/db_gateway_service/routers/reputation.py`**

Add GET endpoints:

```python
@router.get("/feedback/count")
async def count_feedback() -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    return JSONResponse(status_code=200, content={"count": state.db_reader.count_feedback()})

@router.get("/feedback/{feedback_id}")
async def get_feedback(feedback_id: str) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    fb = state.db_reader.get_feedback(feedback_id)
    if fb is None:
        raise ServiceError(error="feedback_not_found", message="Feedback not found", status_code=404, details={})
    return JSONResponse(status_code=200, content=fb)

@router.get("/feedback")
async def list_feedback(request: Request) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    task_id = request.query_params.get("task_id")
    agent_id = request.query_params.get("agent_id")
    if task_id is not None:
        items = state.db_reader.get_feedback_by_task(task_id)
    elif agent_id is not None:
        items = state.db_reader.get_feedback_by_agent(agent_id)
    else:
        items = []
    return JSONResponse(status_code=200, content={"feedback": items})
```

**IMPORTANT:** `/feedback/count` MUST be before `/feedback/{feedback_id}`.

**File: `services/db-gateway/src/db_gateway_service/routers/court.py`**

Add GET endpoints and a POST endpoint for status updates and votes:

```python
@router.get("/claims/count")
async def count_claims() -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    return JSONResponse(status_code=200, content={"count": state.db_reader.count_claims()})

@router.get("/claims/count-active")
async def count_active_claims() -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    return JSONResponse(status_code=200, content={"count": state.db_reader.count_active_claims()})

@router.get("/claims")
async def list_claims(request: Request) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    status = request.query_params.get("status")
    claimant_id = request.query_params.get("claimant_id")
    claims = state.db_reader.list_claims(status, claimant_id)
    return JSONResponse(status_code=200, content={"claims": claims})

@router.get("/claims/{claim_id}")
async def get_claim(claim_id: str) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    claim = state.db_reader.get_claim(claim_id)
    if claim is None:
        raise ServiceError(error="claim_not_found", message="Claim not found", status_code=404, details={})
    return JSONResponse(status_code=200, content=claim)

@router.get("/claims/{claim_id}/rebuttal")
async def get_rebuttal(claim_id: str) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    rebuttal = state.db_reader.get_rebuttal(claim_id)
    if rebuttal is None:
        raise ServiceError(error="rebuttal_not_found", message="No rebuttal for this claim", status_code=404, details={})
    return JSONResponse(status_code=200, content=rebuttal)

@router.get("/rulings/{claim_id}")
async def get_ruling(claim_id: str) -> JSONResponse:
    state = get_app_state()
    if state.db_reader is None:
        raise ServiceError(error="service_not_ready", message="DbReader not initialized", status_code=503, details={})
    ruling = state.db_reader.get_ruling(claim_id)
    if ruling is None:
        raise ServiceError(error="ruling_not_found", message="Ruling not found", status_code=404, details={})
    return JSONResponse(status_code=200, content=ruling)
```

The Court service also needs POST endpoints for operations that the current court.py router doesn't have but the DisputeStorageInterface needs:

1. **POST /court/claims/{claim_id}/status** — update claim status
2. **POST /court/votes** — record a vote

These must be added to both the router AND DbWriter. For the status update, follow the pattern of `/board/tasks/{task_id}/status`. For votes, follow the pattern of `/court/rulings`.

Add to DbWriter a `update_claim_status` method that does:
```sql
UPDATE court_claims SET status = ? WHERE claim_id = ?
```

And a `record_vote` method that inserts into a `court_votes` table — but WAIT: the schema.sql has NO `court_votes` table. The votes are stored in `court_rulings.judge_votes` as a JSON string. However, the local Court service has its own `votes` table. This is a schema divergence.

**Decision for court votes:** The GatewayDisputeStore's `persist_ruling` method should:
1. POST to `/court/rulings` (which already exists and accepts `judge_votes` as a JSON field)
2. Also POST to `/court/claims/{claim_id}/status` to set status to 'ruled'

The `get_votes` method should parse the `judge_votes` JSON string from the ruling record. This avoids needing a separate votes table in the unified schema.

The `revert_to_rebuttal_pending` method needs a dedicated endpoint or can use the status update endpoint. Use POST `/court/claims/{claim_id}/status` with `{"status": "rebuttal_pending"}`.

### Tier 1 Verification

```bash
cd services/db-gateway && just ci-quiet
```

This MUST pass before proceeding to Tier 2.

---

## Tier 2: GatewayLedgerStore (Central Bank — agent-economy-1drj)

### File: `services/central-bank/src/central_bank_service/services/gateway_ledger_store.py` (NEW)

Follow the pattern of `services/identity/src/identity_service/services/gateway_agent_store.py`.

```python
"""DB Gateway-backed ledger storage."""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime
from typing import Any

import httpx

from central_bank_service.logging import get_logger
from service_commons.exceptions import ServiceError

logger = get_logger(__name__)


class GatewayLedgerStore:
    """Ledger storage backed by the DB Gateway HTTP API."""

    def __init__(self, base_url: str, timeout_seconds: int) -> None:
        self._client = httpx.Client(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    def _now(self) -> str:
        return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")

    def _new_tx_id(self) -> str:
        return f"tx-{uuid.uuid4()}"

    def _new_escrow_id(self) -> str:
        return f"esc-{uuid.uuid4()}"

    def create_account(self, account_id: str, initial_balance: int) -> dict[str, object]:
        if initial_balance < 0:
            raise ServiceError("invalid_amount", "Initial balance must be non-negative", 400, {})

        now = self._now()
        payload: dict[str, Any] = {
            "account_id": account_id,
            "balance": initial_balance,
            "created_at": now,
            "event": {
                "event_source": "bank",
                "event_type": "account.created",
                "timestamp": now,
                "agent_id": account_id,
                "summary": f"Account created for {account_id}",
                "payload": json.dumps({"agent_name": account_id}),
            },
        }
        if initial_balance > 0:
            tx_id = self._new_tx_id()
            payload["initial_credit"] = {
                "tx_id": tx_id,
                "amount": initial_balance,
                "reference": "initial_balance",
                "timestamp": now,
            }

        resp = self._client.post("/bank/accounts", json=payload)
        if resp.status_code == 409:
            raise ServiceError("account_exists", "Account already exists for this agent", 409, {})
        if resp.status_code not in (200, 201):
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)

        return {"account_id": account_id, "balance": initial_balance, "created_at": now}

    def get_account(self, account_id: str) -> dict[str, object] | None:
        resp = self._client.get(f"/bank/accounts/{account_id}")
        if resp.status_code == 404:
            return None
        if resp.status_code != 200:
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)
        return resp.json()

    def credit(self, account_id: str, amount: int, reference: str) -> dict[str, object]:
        if amount <= 0:
            raise ServiceError("invalid_amount", "Amount must be a positive integer", 400, {})

        now = self._now()
        tx_id = self._new_tx_id()
        payload: dict[str, Any] = {
            "tx_id": tx_id,
            "account_id": account_id,
            "amount": amount,
            "reference": reference,
            "timestamp": now,
            "event": {
                "event_source": "bank",
                "event_type": "salary.paid",
                "timestamp": now,
                "agent_id": account_id,
                "summary": f"Credited {amount} to {account_id}",
                "payload": json.dumps({"amount": amount}),
            },
        }
        resp = self._client.post("/bank/credit", json=payload)
        if resp.status_code == 404:
            raise ServiceError("account_not_found", "Account not found", 404, {})
        if resp.status_code not in (200, 201):
            # Check for duplicate credit (idempotency)
            data = resp.json() if resp.headers.get("content-type", "").startswith("application/json") else {}
            error_code = data.get("error", "")
            if error_code == "constraint_violation":
                raise ServiceError("payload_mismatch", "Duplicate credit reference", 400, {})
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)

        data = resp.json()
        return {"tx_id": data.get("tx_id", tx_id), "balance_after": data.get("balance_after", 0)}

    def get_transactions(self, account_id: str) -> list[dict[str, object]]:
        account = self.get_account(account_id)
        if account is None:
            raise ServiceError("account_not_found", "Account not found", 404, {})

        resp = self._client.get(f"/bank/accounts/{account_id}/transactions")
        if resp.status_code != 200:
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)
        data = resp.json()
        return data.get("transactions", [])

    def escrow_lock(self, payer_account_id: str, amount: int, task_id: str) -> dict[str, object]:
        if amount <= 0:
            raise ServiceError("invalid_amount", "Amount must be a positive integer", 400, {})

        now = self._now()
        tx_id = self._new_tx_id()
        escrow_id = self._new_escrow_id()
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "payer_account_id": payer_account_id,
            "amount": amount,
            "task_id": task_id,
            "created_at": now,
            "tx_id": tx_id,
            "event": {
                "event_source": "bank",
                "event_type": "escrow.locked",
                "timestamp": now,
                "agent_id": payer_account_id,
                "task_id": task_id,
                "summary": f"Escrow locked: {amount} for task {task_id}",
                "payload": json.dumps({"escrow_id": escrow_id, "amount": amount, "title": task_id}),
            },
        }
        resp = self._client.post("/bank/escrow/lock", json=payload)

        if resp.status_code == 409:
            # Could be duplicate — idempotent
            data = resp.json()
            raise ServiceError("escrow_already_locked", data.get("message", "Escrow already locked"), 409, {})
        if resp.status_code == 404:
            raise ServiceError("account_not_found", "Account not found", 404, {})
        if resp.status_code not in (200, 201):
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)

        return {"escrow_id": escrow_id, "amount": amount, "task_id": task_id, "status": "locked"}

    def escrow_release(self, escrow_id: str, recipient_account_id: str) -> dict[str, object]:
        now = self._now()
        tx_id = self._new_tx_id()
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "recipient_account_id": recipient_account_id,
            "tx_id": tx_id,
            "resolved_at": now,
            "constraints": {"status": "locked"},
            "event": {
                "event_source": "bank",
                "event_type": "escrow.released",
                "timestamp": now,
                "summary": f"Escrow {escrow_id} released to {recipient_account_id}",
                "payload": json.dumps({"escrow_id": escrow_id, "recipient_id": recipient_account_id}),
            },
        }
        resp = self._client.post("/bank/escrow/release", json=payload)

        if resp.status_code == 404:
            raise ServiceError("escrow_not_found", "Escrow not found", 404, {})
        if resp.status_code == 409:
            raise ServiceError("escrow_already_resolved", "Escrow has already been resolved", 409, {})
        if resp.status_code not in (200, 201):
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)

        data = resp.json()
        return {
            "escrow_id": escrow_id,
            "status": "released",
            "recipient": recipient_account_id,
            "amount": data.get("amount", 0),
        }

    def escrow_split(
        self, escrow_id: str, worker_account_id: str, worker_pct: int, poster_account_id: str,
    ) -> dict[str, object]:
        if not (0 <= worker_pct <= 100):
            raise ServiceError("invalid_amount", "worker_pct must be between 0 and 100", 400, {})

        # Need to read escrow to compute amounts
        escrow_resp = self._client.get(f"/bank/escrow/{escrow_id}")
        if escrow_resp.status_code == 404:
            raise ServiceError("escrow_not_found", "Escrow not found", 404, {})
        if escrow_resp.status_code != 200:
            msg = f"Gateway error: {escrow_resp.status_code} {escrow_resp.text}"
            raise RuntimeError(msg)

        escrow_data = escrow_resp.json()
        if escrow_data["status"] != "locked":
            raise ServiceError("escrow_already_resolved", "Escrow has already been resolved", 409, {})
        if poster_account_id != escrow_data["payer_account_id"]:
            raise ServiceError("payload_mismatch", "poster_account_id must match the escrow payer_account_id", 400, {})

        total_amount = int(escrow_data["amount"])
        worker_amount = total_amount * worker_pct // 100
        poster_amount = total_amount - worker_amount

        now = self._now()
        payload: dict[str, Any] = {
            "escrow_id": escrow_id,
            "worker_account_id": worker_account_id,
            "poster_account_id": poster_account_id,
            "worker_amount": worker_amount,
            "poster_amount": poster_amount,
            "worker_tx_id": self._new_tx_id(),
            "poster_tx_id": self._new_tx_id(),
            "resolved_at": now,
            "constraints": {"status": "locked"},
            "event": {
                "event_source": "bank",
                "event_type": "escrow.split",
                "timestamp": now,
                "summary": f"Escrow {escrow_id} split: worker={worker_amount}, poster={poster_amount}",
                "payload": json.dumps({
                    "escrow_id": escrow_id,
                    "worker_amount": worker_amount,
                    "poster_amount": poster_amount,
                }),
            },
        }
        resp = self._client.post("/bank/escrow/split", json=payload)

        if resp.status_code == 409:
            raise ServiceError("escrow_already_resolved", "Escrow has already been resolved", 409, {})
        if resp.status_code not in (200, 201):
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)

        return {
            "escrow_id": escrow_id,
            "status": "split",
            "worker_amount": worker_amount,
            "poster_amount": poster_amount,
        }

    def count_accounts(self) -> int:
        resp = self._client.get("/bank/accounts/count")
        if resp.status_code != 200:
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)
        return int(resp.json()["count"])

    def total_escrowed(self) -> int:
        resp = self._client.get("/bank/escrow/total-locked")
        if resp.status_code != 200:
            msg = f"Gateway error: {resp.status_code} {resp.text}"
            raise RuntimeError(msg)
        return int(resp.json()["total"])

    def close(self) -> None:
        self._client.close()
```

**NOTE:** This uses `httpx.Client` (sync), not `httpx.AsyncClient`, because the `LedgerStorageInterface` protocol has sync methods. The existing identity service uses async because `IdentityStorageInterface` has async methods.

### Wire into lifespan.py

Modify `services/central-bank/src/central_bank_service/core/lifespan.py`:

Replace the ledger initialization with a branch:

```python
if settings.db_gateway is not None:
    from central_bank_service.services.gateway_ledger_store import GatewayLedgerStore
    state.ledger = GatewayLedgerStore(
        base_url=settings.db_gateway.url,
        timeout_seconds=settings.db_gateway.timeout_seconds,
    )
else:
    state.ledger = SqliteLedgerStore(db_path=db_path)
```

When using the gateway store, the `gateway_client` (fire-and-forget) is NOT needed. But keep backward compatibility — if `db_gateway` is set, skip gateway_client initialization since the GatewayLedgerStore handles writes directly.

### Verification

```bash
cd services/central-bank && just ci-quiet
```

---

## Tier 3: GatewayFeedbackStore (Reputation — agent-economy-8s03)

### File: `services/reputation/src/reputation_service/services/gateway_feedback_store.py` (NEW)

Similar to GatewayLedgerStore but implements `FeedbackStorageInterface`.

Key points:
- `insert_feedback` does POST to `/reputation/feedback` (already exists in gateway)
- Mutual reveal logic: the gateway's `submit_feedback` in `db_writer.py` already handles the mutual reveal. Read the gateway's db_writer to verify this.
- `get_by_id` does GET `/reputation/feedback/{feedback_id}`
- `get_by_task` does GET `/reputation/feedback?task_id=X`
- `get_by_agent` does GET `/reputation/feedback?agent_id=X`
- `count` does GET `/reputation/feedback/count`
- Returns `FeedbackRecord` dataclass instances

**CRITICAL:** The `FeedbackStorageInterface` returns `FeedbackRecord` (a dataclass), not raw dicts. The gateway store must convert JSON responses to `FeedbackRecord` objects.

### Wire into lifespan.py

Same pattern as Central Bank: branch on `settings.db_gateway`.

### Verification

```bash
cd services/reputation && just ci-quiet
```

---

## Tier 4: GatewayTaskStore (Task Board — agent-economy-lsno)

### File: `services/task-board/src/task_board_service/services/gateway_task_store.py` (NEW)

Implements `TaskStorageInterface` (which extends `AssetStorageInterface`).

Key points:
- `insert_task` → POST `/board/tasks`
- `get_task` → GET `/board/tasks/{task_id}`
- `update_task` → POST `/board/tasks/{task_id}/status` with `updates` and `constraints`
- `list_tasks` → GET `/board/tasks?status=X&poster_id=Y&worker_id=Z&limit=N&offset=M`
- `count_tasks` → GET `/board/tasks/count`
- `count_tasks_by_status` → GET `/board/tasks/count-by-status`
- `insert_bid` → POST `/board/bids` (gateway atomically increments bid_count)
- `get_bid` → GET `/board/bids/{bid_id}?task_id=X`
- `get_bids_for_task` → GET `/board/tasks/{task_id}/bids`
- `insert_asset` → POST `/board/assets`
- `get_asset` → GET `/board/assets/{asset_id}?task_id=X`
- `get_assets_for_task` → GET `/board/tasks/{task_id}/assets`
- `count_assets` → GET `/board/tasks/{task_id}/assets/count`

**IMPORTANT:** The `update_task` method signature is:
```python
def update_task(self, task_id: str, updates: dict[str, Any], *, expected_status: str | None) -> int:
```
It returns the number of affected rows (0 or 1). The gateway's POST `/board/tasks/{task_id}/status` returns a result dict. Map the response: if 200, return 1; if 409 (constraint violation), return 0.

The `insert_bid` and `insert_task` methods return `None` (void). They raise on errors.

Handle `DuplicateTaskError` and `DuplicateBidError` — the gateway returns 409 on duplicate keys.

### Wire into lifespan.py

Same pattern. Branch on `settings.db_gateway`.

### Verification

```bash
cd services/task-board && just ci-quiet
```

---

## Tier 5: GatewayDisputeStore (Court — agent-economy-nww7)

### File: `services/court/src/court_service/services/gateway_dispute_store.py` (NEW)

Implements `DisputeStorageInterface`.

**SCHEMA MAPPING CHALLENGE:**
The local Court service stores disputes in a single `disputes` table with columns like `claim`, `rebuttal`, `status`, `worker_pct`, `ruling_summary`. The unified schema has SEPARATE tables: `court_claims`, `court_rebuttals`, `court_rulings`.

The GatewayDisputeStore must bridge this gap:

| Local method | Maps to Gateway |
|---|---|
| `insert_dispute(task_id, claimant_id, respondent_id, claim, escrow_id, rebuttal_deadline)` | POST `/court/claims` |
| `get_dispute(dispute_id)` | GET `/court/claims/{claim_id}` + GET `/court/claims/{claim_id}/rebuttal` + GET `/court/rulings/{claim_id}` |
| `get_dispute_row(dispute_id)` | Same as get_dispute but returns sqlite3.Row — PROBLEM: cannot return actual Row. Must return something compatible. |
| `update_rebuttal(dispute_id, rebuttal)` | POST `/court/rebuttals` |
| `set_status(dispute_id, status)` | POST `/court/claims/{claim_id}/status` (new endpoint) |
| `revert_to_rebuttal_pending(dispute_id)` | POST `/court/claims/{claim_id}/status` with status=rebuttal_pending |
| `persist_ruling(dispute_id, worker_pct, ruling_summary, votes)` | POST `/court/rulings` |
| `get_votes(dispute_id)` | Parse from `/court/rulings/{claim_id}` judge_votes JSON |
| `list_disputes(task_id, status)` | GET `/court/claims?status=X` |
| `count_disputes()` | GET `/court/claims/count` |
| `count_active()` | GET `/court/claims/count-active` |

**The `get_dispute_row` problem:** The protocol defines `get_dispute_row(dispute_id) -> sqlite3.Row | None`. This is a leaky abstraction — it returns a sqlite3.Row which doesn't exist in the gateway context. The GatewayDisputeStore should return a dict-like object that behaves like sqlite3.Row (supports `row["column"]` access). A simple dict works since sqlite3.Row supports key access when row_factory is set. Check how the callers use `get_dispute_row` — they likely access it via `row["status"]` etc. A plain dict should work.

**NOTE:** The `DisputeStorageInterface` protocol has `import sqlite3` in TYPE_CHECKING for the `get_dispute_row` return type. The gateway implementation can return a dict that satisfies the same access pattern, but the type annotation will be wrong. The protocol may need to be updated to return `dict[str, Any] | None` instead, OR the gateway store can return a `types.SimpleNamespace` that supports item access. Actually, check if the protocol uses `sqlite3.Row` — yes it does. This may cause type-checking issues.

**WORKAROUND:** Update `services/court/src/court_service/services/protocol.py` to change `get_dispute_row` return type from `sqlite3.Row | None` to `Any` (or `dict[str, Any] | None`). This is the cleanest fix. The SqliteDisputeStore already returns sqlite3.Row which is also a valid dict-like object.

### Wire into lifespan.py

Same pattern. Branch on `settings.db_gateway`.

### Verification

```bash
cd services/court && just ci-quiet
```

---

## Tier 6: Full Verification

After all tiers are complete:

```bash
# Run full project CI
just ci-all-quiet

# Start all services and run E2E if available
just stop-all
just start-all
just status
```

If E2E tests exist:
```bash
cd tests && just test  # or whatever the cross-service test command is
```

---

## Critical Rules

1. **Do NOT use git.** There is no git in this project.
2. **Do NOT modify existing test files.** Add new test files only.
3. **Use `uv run` for all Python execution.** Never use raw `python` or `pip install`.
4. **Run `just ci-quiet` after EACH tier** from the relevant service directory.
5. **Follow existing patterns.** Use `services/identity/src/identity_service/services/gateway_agent_store.py` as the reference implementation.
6. **Route ordering matters in FastAPI.** Static routes like `/count` must be defined BEFORE parameterized routes like `/{id}`.
7. **Sync vs Async:** Identity uses async (`httpx.AsyncClient`). Central Bank, Task Board, Reputation, Court protocols are sync — use `httpx.Client` (sync).
8. **All config comes from config.yaml.** The `db_gateway.url` and `db_gateway.timeout_seconds` settings already exist in all service configs.
9. **Never hardcode values.** No default parameters for configurable settings.
10. **ServiceError for business errors, RuntimeError for gateway communication errors.**

## File Summary

### New Files
- `services/central-bank/src/central_bank_service/services/gateway_ledger_store.py`
- `services/reputation/src/reputation_service/services/gateway_feedback_store.py`
- `services/task-board/src/task_board_service/services/gateway_task_store.py`
- `services/court/src/court_service/services/gateway_dispute_store.py`

### Modified Files
- `services/db-gateway/tests/integration/conftest.py` (port fix)
- `services/db-gateway/src/db_gateway_service/services/db_reader.py` (add bank/board/reputation/court methods)
- `services/db-gateway/src/db_gateway_service/routers/bank.py` (add GET endpoints)
- `services/db-gateway/src/db_gateway_service/routers/board.py` (add GET endpoints)
- `services/db-gateway/src/db_gateway_service/routers/reputation.py` (add GET endpoints)
- `services/db-gateway/src/db_gateway_service/routers/court.py` (add GET + status POST endpoints)
- `services/db-gateway/src/db_gateway_service/services/db_writer.py` (add claim status update, vote recording if needed)
- `services/central-bank/src/central_bank_service/core/lifespan.py` (gateway store branch)
- `services/reputation/src/reputation_service/core/lifespan.py` (gateway store branch)
- `services/task-board/src/task_board_service/core/lifespan.py` (gateway store branch)
- `services/court/src/court_service/core/lifespan.py` (gateway store branch)
- `services/court/src/court_service/services/protocol.py` (fix sqlite3.Row return type)

## Execution Order

1. Tier 0 → `cd services/db-gateway && just ci-quiet`
2. Tier 1A + 1B → `cd services/db-gateway && just ci-quiet`
3. Tier 2 → `cd services/central-bank && just ci-quiet`
4. Tier 3 → `cd services/reputation && just ci-quiet`
5. Tier 4 → `cd services/task-board && just ci-quiet`
6. Tier 5 → `cd services/court && just ci-quiet`
7. Tier 6 → `just ci-all-quiet`

Each tier builds on the previous. Do NOT skip ahead.
