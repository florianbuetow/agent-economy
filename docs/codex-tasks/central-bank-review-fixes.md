# Code Review Fix Tasks

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> Do NOT modify any existing test files in tests/.
> After EACH task, run: `cd services/central-bank && just test`
> Skip `just ci-quiet` — I will run it on my machine. Just run `just test`.
> Commit after each task with a descriptive message.

---

## Task 1: Extract shared router helpers

**Problem:** `_parse_json_body`, `_verify_jws_token`, and `_require_platform` are duplicated identically in `accounts.py` and `escrow.py`.

**Files:**
- Create: `services/central-bank/src/central_bank_service/routers/helpers.py`
- Modify: `services/central-bank/src/central_bank_service/routers/accounts.py`
- Modify: `services/central-bank/src/central_bank_service/routers/escrow.py`

**Steps:**

1. Create `services/central-bank/src/central_bank_service/routers/helpers.py` with these functions moved from the routers:
   - `_parse_json_body(body: bytes) -> dict[str, Any]` — exact same implementation
   - `async _verify_jws_token(token: str) -> dict[str, Any]` — exact same implementation (needs `get_app_state` import)
   - `_require_platform(agent_id: str, platform_agent_id: str) -> None` — exact same implementation
   - `_require_account_owner(verified_agent_id: str, account_id: str) -> None` — move from accounts.py

   Rename them without the underscore prefix since they're now in a helper module:
   - `parse_json_body`
   - `verify_jws_token` (async)
   - `require_platform`
   - `require_account_owner`

2. In `accounts.py`:
   - Remove the 4 helper function definitions
   - Add import: `from central_bank_service.routers.helpers import parse_json_body, verify_jws_token, require_platform, require_account_owner`
   - Replace all calls: `_parse_json_body` → `parse_json_body`, `_verify_jws_token` → `verify_jws_token`, `_require_platform` → `require_platform`, `_require_account_owner` → `require_account_owner`

3. In `escrow.py`:
   - Remove the 3 helper function definitions (`_parse_json_body`, `_verify_jws_token`, `_require_platform`)
   - Add import: `from central_bank_service.routers.helpers import parse_json_body, verify_jws_token, require_platform`
   - Replace all calls: `_parse_json_body` → `parse_json_body`, `_verify_jws_token` → `verify_jws_token`, `_require_platform` → `require_platform`

4. Run `cd services/central-bank && just test` to verify all tests still pass.

5. Commit: `refactor(central-bank): extract shared router helpers`

---

## Task 2: Validate platform.agent_id is non-empty at startup

**Problem:** `config.yaml` has `platform.agent_id: ""` which passes Pydantic validation, allowing the service to start with an empty platform agent ID. This is a security risk.

**File:** `services/central-bank/src/central_bank_service/config.py`

**Steps:**

1. Add a `field_validator` to `PlatformConfig`:

```python
from pydantic import BaseModel, ConfigDict, field_validator

class PlatformConfig(BaseModel):
    """Platform agent configuration."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str

    @field_validator("agent_id")
    @classmethod
    def agent_id_must_not_be_empty(cls, v: str) -> str:
        """Reject empty platform agent_id at startup."""
        if not v.strip():
            msg = "platform.agent_id must not be empty"
            raise ValueError(msg)
        return v
```

2. Update `config.yaml` to set a placeholder value that will be overridden in production:

Change line 26 from:
```yaml
  agent_id: ""
```
to:
```yaml
  agent_id: "a-platform-placeholder"
```

3. Run `cd services/central-bank && just test` — all tests should still pass because the test conftest already sets `agent_id: "a-platform-test-id"`.

4. Commit: `fix(central-bank): reject empty platform.agent_id at startup`

---

## Task 3: Fix TOCTOU race conditions in Ledger

**Problem:** All balance-mutating methods in the Ledger do SELECT-then-UPDATE, creating a time-of-check-to-time-of-use race condition. Fix by using atomic UPDATE statements.

**File:** `services/central-bank/src/central_bank_service/services/ledger.py`

**Steps:**

### 3a. Fix `credit` method (lines 150-200)

Replace the current read-then-write pattern:
```python
account = self.get_account(account_id)
if account is None:
    raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

current_balance = cast("int", account["balance"])
new_balance = current_balance + amount

self._db.execute(
    "UPDATE accounts SET balance = ? WHERE account_id = ?",
    (new_balance, account_id),
)
```

With an atomic update:
```python
cursor = self._db.execute(
    "UPDATE accounts SET balance = balance + ? WHERE account_id = ?",
    (amount, account_id),
)
if cursor.rowcount == 0:
    raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

row = self._db.execute(
    "SELECT balance FROM accounts WHERE account_id = ?",
    (account_id,),
).fetchone()
new_balance = cast("int", row[0])
```

### 3b. Fix `escrow_lock` method (lines 230-293)

Replace the current read-then-write pattern:
```python
account = self.get_account(payer_account_id)
if account is None:
    raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})

current_balance = cast("int", account["balance"])
if current_balance < amount:
    raise ServiceError(
        "INSUFFICIENT_FUNDS",
        "Insufficient funds for escrow lock",
        402,
        {},
    )

...
new_balance = current_balance - amount

self._db.execute(
    "UPDATE accounts SET balance = ? WHERE account_id = ?",
    (new_balance, payer_account_id),
)
```

With an atomic update:
```python
cursor = self._db.execute(
    "UPDATE accounts SET balance = balance - ? WHERE account_id = ? AND balance >= ?",
    (amount, payer_account_id, amount),
)
if cursor.rowcount == 0:
    # Distinguish between not found and insufficient funds
    account = self.get_account(payer_account_id)
    if account is None:
        raise ServiceError("ACCOUNT_NOT_FOUND", "Account not found", 404, {})
    raise ServiceError(
        "INSUFFICIENT_FUNDS",
        "Insufficient funds for escrow lock",
        402,
        {},
    )

row = self._db.execute(
    "SELECT balance FROM accounts WHERE account_id = ?",
    (payer_account_id,),
).fetchone()
new_balance = cast("int", row[0])
```

### 3c. Fix `escrow_release` method (lines 295-355)

Replace:
```python
recipient = self.get_account(recipient_account_id)
if recipient is None:
    raise ServiceError("ACCOUNT_NOT_FOUND", "Recipient account not found", 404, {})

amount = cast("int", escrow["amount"])
now = self._now()
tx_id = self._new_tx_id()
new_balance = cast("int", recipient["balance"]) + amount

self._db.execute(
    "UPDATE accounts SET balance = ? WHERE account_id = ?",
    (new_balance, recipient_account_id),
)
```

With:
```python
amount = cast("int", escrow["amount"])
now = self._now()
tx_id = self._new_tx_id()

cursor = self._db.execute(
    "UPDATE accounts SET balance = balance + ? WHERE account_id = ?",
    (amount, recipient_account_id),
)
if cursor.rowcount == 0:
    raise ServiceError("ACCOUNT_NOT_FOUND", "Recipient account not found", 404, {})

row = self._db.execute(
    "SELECT balance FROM accounts WHERE account_id = ?",
    (recipient_account_id,),
).fetchone()
new_balance = cast("int", row[0])
```

### 3d. Fix `escrow_split` method (lines 357-463)

Apply the same atomic UPDATE pattern for both worker and poster credits. For each:

Replace:
```python
worker_new_balance = cast("int", worker["balance"]) + worker_amount
...
self._db.execute(
    "UPDATE accounts SET balance = ? WHERE account_id = ?",
    (worker_new_balance, worker_account_id),
)
```

With:
```python
self._db.execute(
    "UPDATE accounts SET balance = balance + ? WHERE account_id = ?",
    (worker_amount, worker_account_id),
)
row = self._db.execute(
    "SELECT balance FROM accounts WHERE account_id = ?",
    (worker_account_id,),
).fetchone()
worker_new_balance = cast("int", row[0])
```

Do the same for the poster credit block.

Also, move the account existence checks to the top using `get_account` BEFORE the atomic updates, since we need to verify accounts exist before starting the split. Keep:
```python
worker = self.get_account(worker_account_id)
if worker is None:
    raise ServiceError("ACCOUNT_NOT_FOUND", "Worker account not found", 404, {})

poster = self.get_account(poster_account_id)
if poster is None:
    raise ServiceError("ACCOUNT_NOT_FOUND", "Poster account not found", 404, {})
```

But use them only for existence checks, not for reading the balance to do arithmetic.

5. Run `cd services/central-bank && just test` to verify all tests pass.

6. Commit: `fix(central-bank): use atomic UPDATE to prevent TOCTOU race conditions`

---

## Task 4: Add rollback guards to Ledger methods

**Problem:** `credit`, `escrow_release`, and `escrow_split` don't have try/except rollback guards. If an INSERT fails after an UPDATE, the partial changes are not rolled back.

**File:** `services/central-bank/src/central_bank_service/services/ledger.py`

**Steps:**

1. Wrap the multi-statement transaction blocks in `credit`, `escrow_release`, and `escrow_split` with try/except that calls `self._db.rollback()` on failure:

Pattern (apply to all three methods):
```python
try:
    # ... all the self._db.execute() calls ...
    self._db.commit()
except ServiceError:
    self._db.rollback()
    raise
except Exception:
    self._db.rollback()
    raise
```

Note: `create_account` already has this pattern — follow it exactly.

For `credit`: wrap from the atomic UPDATE through the commit.
For `escrow_release`: wrap from the atomic UPDATE through the commit.
For `escrow_split`: wrap from the first UPDATE through the commit.
For `escrow_lock`: also wrap from the atomic UPDATE through the commit (it doesn't have a guard either).

2. Run `cd services/central-bank && just test`

3. Commit: `fix(central-bank): add rollback guards to all Ledger mutation methods`

---

## Task 5: Cross-check URL parameters against JWS payload

**Problem:** `credit_account` uses URL `account_id` without verifying it matches the JWS payload. `escrow_release` and `escrow_split` use URL `escrow_id` without payload cross-check. An attacker could sign a valid token for one resource but use the URL to target a different resource.

**Files:**
- `services/central-bank/src/central_bank_service/routers/accounts.py`
- `services/central-bank/src/central_bank_service/routers/escrow.py`

**Steps:**

### 5a. Fix `credit_account` in accounts.py

After extracting payload fields, add this cross-check:

```python
    payload_account_id = payload.get("account_id")
    if not payload_account_id or not isinstance(payload_account_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing account_id in JWS payload", 400, {})
    if payload_account_id != account_id:
        raise ServiceError(
            "PAYLOAD_MISMATCH",
            "JWS payload account_id does not match URL",
            400,
            {},
        )
```

Add this BEFORE the `state.ledger.credit(...)` call.

### 5b. Fix `escrow_release` in escrow.py

After extracting `recipient_account_id` from payload, add:

```python
    payload_escrow_id = payload.get("escrow_id")
    if not payload_escrow_id or not isinstance(payload_escrow_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing escrow_id in JWS payload", 400, {})
    if payload_escrow_id != escrow_id:
        raise ServiceError(
            "PAYLOAD_MISMATCH",
            "JWS payload escrow_id does not match URL",
            400,
            {},
        )
```

Add this BEFORE the `state.ledger.escrow_release(...)` call.

### 5c. Fix `escrow_split` in escrow.py

After extracting `poster_account_id` from payload, add:

```python
    payload_escrow_id = payload.get("escrow_id")
    if not payload_escrow_id or not isinstance(payload_escrow_id, str):
        raise ServiceError("INVALID_PAYLOAD", "Missing escrow_id in JWS payload", 400, {})
    if payload_escrow_id != escrow_id:
        raise ServiceError(
            "PAYLOAD_MISMATCH",
            "JWS payload escrow_id does not match URL",
            400,
            {},
        )
```

Add this BEFORE the `state.ledger.escrow_split(...)` call.

4. Run `cd services/central-bank && just test` — existing tests should still pass because the test JWS payloads already include `escrow_id` and the URLs match.

5. Commit: `fix(central-bank): cross-check URL params against JWS payload`

---

## Task 6: Remove silent defaults for initial_balance and reference

**Problem:** `initial_balance` defaults to `0` and `reference` defaults to `""` in the routers. CLAUDE.md says "Never assume any default values anywhere."

**File:** `services/central-bank/src/central_bank_service/routers/accounts.py`

**Steps:**

1. In `create_account`, change:
```python
initial_balance = payload.get("initial_balance", 0)
```
to:
```python
initial_balance = payload.get("initial_balance")
if initial_balance is None:
    raise ServiceError(
        "INVALID_PAYLOAD",
        "Missing initial_balance in JWS payload",
        400,
        {},
    )
```

2. In `credit_account`, change:
```python
reference = payload.get("reference", "")
```
to:
```python
reference = payload.get("reference")
if reference is None:
    raise ServiceError(
        "INVALID_PAYLOAD",
        "Missing reference in JWS payload",
        400,
        {},
    )
```

3. Run `cd services/central-bank && just test` — tests should pass because they already provide both fields.

4. Commit: `fix(central-bank): require explicit initial_balance and reference in JWS payload`

---

## Task 7: Add new unit tests for review findings

**Problem:** Tests don't cover JWS verification failure (`valid: false`) propagation, payload mismatch errors, or missing required fields.

**Files:**
- Create: `services/central-bank/tests/unit/routers/test_review_fixes.py`

Do NOT modify existing test files.

**Steps:**

1. Create `test_review_fixes.py` with these test classes:

```python
"""Tests for code review fix findings."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock

import pytest

from central_bank_service.core.state import get_app_state

from .conftest import PLATFORM_AGENT_ID, make_jws_token


def _setup_identity_mock(state: Any) -> None:
    """Configure mock identity client that decodes tokens."""
    import base64
    import json

    async def mock_verify_jws(token: str) -> dict[str, Any]:
        parts = token.split(".")
        header_b64 = parts[0]
        padding = 4 - len(header_b64) % 4
        if padding != 4:
            header_b64 += "=" * padding
        header = json.loads(base64.urlsafe_b64decode(header_b64))
        payload_b64 = parts[1]
        padding = 4 - len(payload_b64) % 4
        if padding != 4:
            payload_b64 += "=" * padding
        payload = json.loads(base64.urlsafe_b64decode(payload_b64))
        return {"valid": True, "agent_id": header["kid"], "payload": payload}

    state.identity_client.verify_jws = AsyncMock(side_effect=mock_verify_jws)
    state.identity_client.get_agent = AsyncMock(
        return_value={"agent_id": "a-test-agent", "name": "Test"}
    )


@pytest.mark.unit
class TestJWSVerificationFailure:
    """Tests for valid:false propagation from Identity service."""

    async def test_create_account_invalid_jws_returns_403(self, client, platform_keypair):
        """Invalid JWS signature returns 403 FORBIDDEN."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            return_value={"valid": False, "reason": "signature mismatch"}
        )

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test", "initial_balance": 0},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_escrow_lock_invalid_jws_returns_403(self, client, agent_keypair):
        """Invalid JWS on escrow lock returns 403."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            return_value={"valid": False, "reason": "signature mismatch"}
        )

        agent_key, _ = agent_keypair
        token = make_jws_token(
            agent_key,
            "a-agent",
            {"action": "escrow_lock", "agent_id": "a-agent", "amount": 10, "task_id": "T-1"},
        )
        response = await client.post("/escrow/lock", json={"token": token})
        assert response.status_code == 403
        assert response.json()["error"] == "FORBIDDEN"

    async def test_credit_invalid_jws_returns_403(self, client, platform_keypair):
        """Invalid JWS on credit returns 403."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            return_value={"valid": False, "reason": "signature mismatch"}
        )

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-test", "amount": 10, "reference": "test"},
        )
        response = await client.post("/accounts/a-test/credit", json={"token": token})
        assert response.status_code == 403

    async def test_get_balance_invalid_jws_returns_403(self, client, agent_keypair):
        """Invalid JWS on balance check returns 403."""
        state = get_app_state()
        state.identity_client.verify_jws = AsyncMock(
            return_value={"valid": False, "reason": "signature mismatch"}
        )

        agent_key, _ = agent_keypair
        token = make_jws_token(agent_key, "a-agent", {"action": "get_balance"})
        response = await client.get(
            "/accounts/a-agent",
            headers={"Authorization": f"Bearer {token}"},
        )
        assert response.status_code == 403


@pytest.mark.unit
class TestPayloadMismatch:
    """Tests for URL-vs-payload cross-check."""

    async def test_credit_payload_mismatch_returns_400(self, client, platform_keypair):
        """Credit with mismatched URL and payload account_id returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        private_key, _ = platform_keypair
        # Token says account_id is "a-alice" but URL says "a-bob"
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-alice", "amount": 10, "reference": "test"},
        )
        response = await client.post("/accounts/a-bob/credit", json={"token": token})
        assert response.status_code == 400
        assert response.json()["error"] == "PAYLOAD_MISMATCH"

    async def test_escrow_release_payload_mismatch_returns_400(self, client, platform_keypair):
        """Release with mismatched URL and payload escrow_id returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_release",
                "escrow_id": "esc-real",
                "recipient_account_id": "a-worker",
            },
        )
        response = await client.post(
            "/escrow/esc-fake/release",
            json={"token": token},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "PAYLOAD_MISMATCH"

    async def test_escrow_split_payload_mismatch_returns_400(self, client, platform_keypair):
        """Split with mismatched URL and payload escrow_id returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {
                "action": "escrow_split",
                "escrow_id": "esc-real",
                "worker_account_id": "a-worker",
                "worker_pct": 50,
                "poster_account_id": "a-poster",
            },
        )
        response = await client.post(
            "/escrow/esc-fake/split",
            json={"token": token},
        )
        assert response.status_code == 400
        assert response.json()["error"] == "PAYLOAD_MISMATCH"


@pytest.mark.unit
class TestMissingRequiredFields:
    """Tests for required fields that used to have silent defaults."""

    async def test_create_account_missing_initial_balance(self, client, platform_keypair):
        """Missing initial_balance in payload returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        private_key, _ = platform_keypair
        token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test"},
        )
        response = await client.post("/accounts", json={"token": token})
        assert response.status_code == 400

    async def test_credit_missing_reference(self, client, platform_keypair):
        """Missing reference in credit payload returns 400."""
        state = get_app_state()
        _setup_identity_mock(state)

        # Create account first
        create_token = make_jws_token(
            private_key := platform_keypair[0],
            PLATFORM_AGENT_ID,
            {"action": "create_account", "agent_id": "a-test", "initial_balance": 100},
        )
        await client.post("/accounts", json={"token": create_token})

        # Try credit without reference
        credit_token = make_jws_token(
            private_key,
            PLATFORM_AGENT_ID,
            {"action": "credit", "account_id": "a-test", "amount": 10},
        )
        response = await client.post("/accounts/a-test/credit", json={"token": credit_token})
        assert response.status_code == 400
```

2. Run `cd services/central-bank && just test` to verify all tests pass.

3. Commit: `test(central-bank): add tests for review findings`

---

## Summary

Execute tasks 1 through 7 in order. Each task builds on the previous.
After each task, run `cd services/central-bank && just test` and commit.
Do NOT run `just ci-quiet` — I will run it myself.
