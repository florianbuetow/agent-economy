# Fix 4 E2E Test Bugs (6 Failing Tests)

## Overview

There are 4 bugs causing 6 e2e test failures. Each fix is a 1-3 line change in existing code. No new files are needed.

## Pre-requisites

Read these files FIRST before making any changes:
1. `AGENTS.md` — project conventions
2. This plan file

## Bug 1: `escrow_pending` missing from TASK_UPDATE_COLUMNS

**Failing tests (3):**
- `agents/tests/e2e/test_task_board.py::test_execution_deadline_expiry`
- `agents/tests/e2e/test_task_board.py::test_auto_approve_on_review_timeout`
- `agents/tests/e2e/test_task_board_auth.py::test_bidding_deadline_expiry_rejects_bid`

**Root cause:** The `DeadlineEvaluator` sets `escrow_pending` when updating task status via the DB gateway. The `board_tasks` table already has an `escrow_pending` column (see `docs/specifications/schema.sql`), and the `create_task` writer already handles it. But the `TASK_UPDATE_COLUMNS` whitelist in `db_writer.py` does not include `escrow_pending`, so `update_task_status()` rejects it with `Unknown column: escrow_pending`.

**File to edit:** `services/db-gateway/src/db_gateway_service/services/db_writer.py`

**Fix:** Add `"escrow_pending"` to the `TASK_UPDATE_COLUMNS` frozenset at line 13-32. Add it after `"expired_at"`.

**Before:**
```python
TASK_UPDATE_COLUMNS: frozenset[str] = frozenset(
    {
        "status",
        ...
        "expired_at",
    }
)
```

**After:**
```python
TASK_UPDATE_COLUMNS: frozenset[str] = frozenset(
    {
        "status",
        ...
        "expired_at",
        "escrow_pending",
    }
)
```

## Bug 2: Missing 402 handling in `escrow_lock()`

**Failing test (1):**
- `agents/tests/e2e/test_bank.py::test_escrow_lock_insufficient_funds`

**Root cause:** The DB gateway returns HTTP 402 when an escrow lock fails due to insufficient funds. But `gateway_ledger_store.escrow_lock()` only handles 404, 409, and success (200/201). The 402 response falls through to a generic `RuntimeError` which becomes a 500.

**File to edit:** `services/central-bank/src/central_bank_service/services/gateway_ledger_store.py`

**Fix:** Add a 402 handler between the existing 404 check and the 409 check (around line 194). The handler should raise a `ServiceError` with error code `"insufficient_funds"`, message `"Account balance is less than the escrow amount"`, and status code 402.

**Before (lines 192-199):**
```python
if response.status_code == 404:
    raise ServiceError("account_not_found", "Account not found", 404, {})
if response.status_code == 409:
    msg = self._json(response).get("message", "Escrow already locked")
    raise ServiceError("escrow_already_locked", str(msg), 409, {})
if response.status_code not in (200, 201):
```

**After:**
```python
if response.status_code == 404:
    raise ServiceError("account_not_found", "Account not found", 404, {})
if response.status_code == 402:
    raise ServiceError("insufficient_funds", "Account balance is less than the escrow amount", 402, {})
if response.status_code == 409:
    msg = self._json(response).get("message", "Escrow already locked")
    raise ServiceError("escrow_already_locked", str(msg), 409, {})
if response.status_code not in (200, 201):
```

## Bug 3: No UNIQUE constraint on `task_id` in `court_claims`

**Failing test (1):**
- `agents/tests/e2e/test_court_rulings.py::test_duplicate_dispute_rejected`

**Root cause:** The `GatewayDisputeStore.insert_dispute()` generates a new unique `claim_id` each call. Since `court_claims` only has a PRIMARY KEY on `claim_id` (no UNIQUE on `task_id`), duplicate disputes for the same task are silently allowed. The in-memory store prevents this via a dict lookup, but the DB schema does not enforce it.

**File to edit:** `docs/specifications/schema.sql`

**Fix:** Add `UNIQUE` to the `task_id` column in the `court_claims` table definition.

**Before:**
```sql
CREATE TABLE court_claims (
    claim_id        TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL,
```

**After:**
```sql
CREATE TABLE court_claims (
    claim_id        TEXT PRIMARY KEY,
    task_id         TEXT NOT NULL UNIQUE,
```

## Bug 4: Idempotent `create_account` returns success instead of 409

**Failing test (1):**
- `agents/tests/e2e/test_agent_startup.py::test_create_account_is_idempotent`

**Root cause:** The DB gateway's `create_account()` method has idempotency logic: if an account already exists with the same balance, it returns success instead of 409. The test expects that creating an account twice always returns 409 on the second call. Both calls use `initial_balance=0`, so the idempotency check matches and the second call succeeds.

**File to edit:** `services/db-gateway/src/db_gateway_service/services/db_writer.py`

**Fix:** In the `create_account()` method (around line 320), remove the idempotency shortcut (lines 330-333). The IntegrityError handler should always raise `ServiceError("account_exists", ...)` for PK violations.

**Before (lines 320-336):**
```python
except sqlite3.IntegrityError as exc:
    self._db.rollback()
    error_msg = str(exc).lower()
    if "foreign" in error_msg:
        raise ServiceError(
            "foreign_key_violation",
            "Foreign key constraint failed",
            409,
            {},
        ) from exc
    # PK violation — check idempotency
    existing = self._lookup_account(data["account_id"])
    if existing is not None and existing["balance"] == data["balance"]:
        return {"account_id": data["account_id"], "event_id": 0}
    raise ServiceError(
```

**After:**
```python
except sqlite3.IntegrityError as exc:
    self._db.rollback()
    error_msg = str(exc).lower()
    if "foreign" in error_msg:
        raise ServiceError(
            "foreign_key_violation",
            "Foreign key constraint failed",
            409,
            {},
        ) from exc
    raise ServiceError(
```

## Verification

After making ALL 4 fixes:

1. Restart all services:
   ```bash
   just stop-all && just start-all
   ```

2. Run the e2e tests:
   ```bash
   just test-e2e
   ```

3. All 6 previously failing tests must now pass:
   - `test_create_account_is_idempotent`
   - `test_escrow_lock_insufficient_funds`
   - `test_duplicate_dispute_rejected`
   - `test_execution_deadline_expiry`
   - `test_auto_approve_on_review_timeout`
   - `test_bidding_deadline_expiry_rejects_bid`

4. Run per-service CI for the affected services:
   ```bash
   cd services/db-gateway && just ci-quiet
   cd services/central-bank && just ci-quiet
   ```

## Do NOT

- Do NOT modify any test files
- Do NOT create new files
- Do NOT add imports — all needed imports are already present
- Do NOT change any logic beyond the specific fixes described above
