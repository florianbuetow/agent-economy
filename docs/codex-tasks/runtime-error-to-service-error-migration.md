# Replace RuntimeError with ServiceError in Router Precondition Checks

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> After EACH task, run the service's `just test` from the service directory.
> After ALL tasks in a service, run `just ci-quiet` from the service directory.
> Commit after each task with a descriptive message.

## Background

The endpoint error handling specification (`docs/specifications/endpoint-error-handling.md`) requires:

> **Check preconditions** — raise `ServiceError(status_code=503)` if the service is not ready.

Currently, all router files use `raise RuntimeError(msg)` for precondition checks (e.g., "Ledger not initialized"). This means:
- The `unhandled_exception_handler` catches these and returns a **generic 500** instead of a structured **503**
- Clients see `{"error": "internal_error", "message": "An unexpected error occurred", "details": {}}` instead of a useful error code and message
- The error code and component name are lost

### The transformation

Every instance of this pattern in **router files only**:

```python
if state.component is None:
    msg = "ComponentName not initialized"
    raise RuntimeError(msg)
```

Must become:

```python
if state.component is None:
    raise ServiceError(
        error="service_not_ready",
        message="ComponentName not initialized",
        status_code=503,
        details={},
    )
```

### Scope

**IN SCOPE — router files only:**
- `services/*/src/*/routers/*.py` — these are endpoint-facing and must return structured errors

**OUT OF SCOPE — do NOT change:**
- `services/*/src/*/core/state.py` — singleton guard, RuntimeError is correct here (programming error, not request error)
- `services/*/src/*/core/lifespan.py` — startup assertions, RuntimeError is correct
- `services/*/src/*/services/*.py` — internal invariant checks, RuntimeError is correct (the unhandled handler catches them as 500 which is appropriate for logic bugs)

### Error code

Use `"service_not_ready"` as the error code for ALL precondition checks in routers. This is a single, stable code that clients can switch on.

**One exception:** In `task-board/routers/assets.py` line 32, there is a `RuntimeError("Authorization token must be present")`. This is NOT a precondition check — it's a request validation error. Use error code `"unauthorized"` with status 401 for this one.

## Important Rules

1. **Only change router files** — `routers/*.py` under each service's `src/` directory.
2. **Add the ServiceError import** — each router file needs `from service_commons.exceptions import ServiceError` (check if already imported).
3. **Do NOT change the message text** — keep the existing message string (e.g., "Ledger not initialized").
4. **Use status_code=503** for all component-not-initialized checks.
5. **Use status_code=401** for the authorization token check in `task-board/routers/assets.py`.
6. **Use `details={}`** — empty dict, no additional context needed.
7. **Update tests that assert on RuntimeError** — some unit tests mock or assert on RuntimeError; these must be updated to assert on ServiceError with status 503.
8. **Do NOT change services layer or core layer** — only routers.

## Important Files to Read First

- `docs/specifications/endpoint-error-handling.md` — the spec this migration enforces
- `AGENTS.md` — project conventions (same as CLAUDE.md)
- `libs/service-commons/src/service_commons/exceptions.py` — ServiceError class definition

---

## Task 1: Identity Service

**Service directory:** `services/identity/`

### Source files to modify

**`src/identity_service/routers/agents.py`** — 5 RuntimeError raises:
- Line 85: `state.registry is None` → ServiceError("service_not_ready", "Registry not initialized", 503, {})
- Line 107: same pattern
- Line 127: same pattern
- Line 178: same pattern
- Line 195: same pattern

Add `from service_commons.exceptions import ServiceError` if not already imported.

### Test files to check

- `tests/unit/routers/test_*.py` — check for any assertions on RuntimeError from these endpoints; update to assert on 503 + `{"error": "service_not_ready"}`

### Verification

```bash
cd services/identity && just test && just ci-quiet
```

### Commit message

```
refactor(identity): replace RuntimeError with ServiceError in router preconditions
```

---

## Task 2: Central Bank Service

**Service directory:** `services/central-bank/`

### Source files to modify

**`src/central_bank_service/routers/accounts.py`** — 5 RuntimeError raises:
- Line 40: `state.ledger is None` → ServiceError("service_not_ready", "Ledger not initialized", 503, {})
- Line 92: `state.identity_client is None` → ServiceError("service_not_ready", "Identity client not initialized", 503, {})
- Line 127: `state.ledger is None` → same as line 40
- Line 211: same
- Line 257: same

**`src/central_bank_service/routers/escrow.py`** — 3 RuntimeError raises:
- Line 67: `state.ledger is None` → ServiceError("service_not_ready", "Ledger not initialized", 503, {})
- Line 125: same
- Line 195: same

**`src/central_bank_service/routers/helpers.py`** — 1 RuntimeError raise:
- Line 44: `state.platform_agent is None` → ServiceError("service_not_ready", "Platform agent not initialized", 503, {})

Add `from service_commons.exceptions import ServiceError` to each file if not already imported.

### Test files to check

- `tests/unit/routers/test_*.py` — update RuntimeError assertions to ServiceError/503

### Verification

```bash
cd services/central-bank && just test && just ci-quiet
```

### Commit message

```
refactor(central-bank): replace RuntimeError with ServiceError in router preconditions
```

---

## Task 3: Task Board Service

**Service directory:** `services/task-board/`

### Source files to modify

**`src/task_board_service/routers/tasks.py`** — 8 RuntimeError raises:
- Lines 35, 77, 104, 125, 146, 167, 188, 264: `state.task_manager is None` → ServiceError("service_not_ready", "TaskManager not initialized", 503, {})

**`src/task_board_service/routers/bids.py`** — 3 RuntimeError raises:
- Lines 37, 58, 78: `state.task_manager is None` → ServiceError("service_not_ready", "TaskManager not initialized", 503, {})

**`src/task_board_service/routers/assets.py`** — 4 RuntimeError raises:
- Line 32: `token is None` → ServiceError("unauthorized", "Authorization token must be present", 401, {}) ← **different error code and status!**
- Lines 62, 85, 101: `state.asset_manager is None` → ServiceError("service_not_ready", "AssetManager not initialized", 503, {})

Add `from service_commons.exceptions import ServiceError` to each file if not already imported.

### Test files to check

- `tests/unit/routers/test_*.py` — update RuntimeError assertions to ServiceError/503 (or 401 for the auth token case)
- `tests/unit/test_state.py` — lines 42-44 and 63 test `get_app_state()` raising RuntimeError — **do NOT change these**, they test the state module, not routers

### Verification

```bash
cd services/task-board && just test && just ci-quiet
```

### Commit message

```
refactor(task-board): replace RuntimeError with ServiceError in router preconditions
```

---

## Task 4: Reputation Service

**Service directory:** `services/reputation/`

### Source files to modify

**`src/reputation_service/routers/feedback.py`** — 5 RuntimeError raises:
- Line 148: `state.platform_agent is None` → ServiceError("service_not_ready", "Platform agent not initialized", 503, {})
- Line 151: `state.feedback_store is None` → ServiceError("service_not_ready", "Feedback store not initialized", 503, {})
- Line 238: same as 151
- Line 261: same
- Line 284: same

Add `from service_commons.exceptions import ServiceError` if not already imported.

### Test files to check

- `tests/unit/routers/test_*.py` — update RuntimeError assertions to ServiceError/503

### Verification

```bash
cd services/reputation && just test && just ci-quiet
```

### Commit message

```
refactor(reputation): replace RuntimeError with ServiceError in router preconditions
```

---

## Task 5: Court Service

**Service directory:** `services/court/`

### Source files to modify

**`src/court_service/routers/disputes.py`** — 9 RuntimeError raises:
- Line 31: `state.platform_agent is None` → ServiceError("service_not_ready", "Platform agent not initialized", 503, {})
- Line 62: `state.dispute_service is None` → ServiceError("service_not_ready", "Dispute service not initialized", 503, {})
- Line 65: `state.platform_agent is None` → same as line 31
- Line 108: `state.dispute_service is None` → same as line 62
- Line 111: `state.platform_agent is None` → same as line 31
- Line 147: `state.dispute_service is None` → same as line 62
- Line 150: `state.platform_agent is None` → same as line 31
- Line 196: `state.dispute_service is None` → same as line 62
- Line 210: `state.dispute_service is None` → same as line 62

**`src/court_service/routers/validation.py`** — 1 RuntimeError raise:
- Line 63: `platform_agent is None` → ServiceError("service_not_ready", "Platform agent not initialized", 503, {})

Add `from service_commons.exceptions import ServiceError` to each file if not already imported.

### Test files to check

- `tests/unit/routers/test_disputes.py` — line 1185-1186 tests Identity RuntimeError returning 502; check if this is still correct after migration (it's about the Identity *client* throwing RuntimeError, not a precondition check — likely no change needed)
- Other test files — update any RuntimeError assertions on precondition checks to ServiceError/503

### Verification

```bash
cd services/court && just test && just ci-quiet
```

### Commit message

```
refactor(court): replace RuntimeError with ServiceError in router preconditions
```

---

## Task 6: DB Gateway Service

**Service directory:** `services/db-gateway/`

### Source files to modify

**`src/db_gateway_service/routers/bank.py`** — 6 RuntimeError raises:
- Lines 48, 66, 87, 107, 136: `state.db_writer is None` → ServiceError("service_not_ready", "DbWriter not initialized", 503, {})
- (Line 136 is the 6th — check all occurrences)

**`src/db_gateway_service/routers/board.py`** — 4 RuntimeError raises:
- Lines 48, 68, 108, 137: `state.db_writer is None` → ServiceError("service_not_ready", "DbWriter not initialized", 503, {})

**`src/db_gateway_service/routers/court.py`** — 3 RuntimeError raises:
- Lines 40, 60, 88: `state.db_writer is None` → ServiceError("service_not_ready", "DbWriter not initialized", 503, {})

**`src/db_gateway_service/routers/identity.py`** — 1 RuntimeError raise:
- Line 29: `state.db_writer is None` → ServiceError("service_not_ready", "DbWriter not initialized", 503, {})

**`src/db_gateway_service/routers/reputation.py`** — 1 RuntimeError raise:
- Line 41: `state.db_writer is None` → ServiceError("service_not_ready", "DbWriter not initialized", 503, {})

Add `from service_commons.exceptions import ServiceError` to each file if not already imported.

### Test files to check

- `tests/unit/routers/test_*.py` — update RuntimeError assertions to ServiceError/503

### Verification

```bash
cd services/db-gateway && just test && just ci-quiet
```

### Commit message

```
refactor(db-gateway): replace RuntimeError with ServiceError in router preconditions
```

---

## Task 7: Observatory and UI Services

**Service directory:** `services/observatory/` and `services/ui/`

Observatory and UI have **no RuntimeError raises in their router files** — no changes needed.

**Bonus fix for observatory:** In `src/observatory_service/routers/events.py`, change 4 instances of `details=None` to `details={}` for consistency with the spec. These are on the ServiceError raises at lines 32, 34, 48, and 54. (The ServiceError constructor converts None to {} automatically, but explicit is better.)

### Verification

```bash
cd services/observatory && just test && just ci-quiet
```

### Commit message

```
fix(observatory): use explicit empty dict for ServiceError details
```

---

## Task 8: Final Verification

Run full project CI to verify all services pass:

```bash
cd /Users/flo/Developer/github/agent-economy && just ci-all-quiet
```

All 8 services must pass all CI checks. If any fail, fix them before proceeding.

---

## Summary Table

| Service | Router RuntimeErrors | Files | Error Code |
|---------|---------------------|-------|------------|
| identity | 5 | agents.py | service_not_ready (503) |
| central-bank | 9 | accounts.py, escrow.py, helpers.py | service_not_ready (503) |
| task-board | 15 | tasks.py, bids.py, assets.py | service_not_ready (503) + unauthorized (401) |
| reputation | 5 | feedback.py | service_not_ready (503) |
| court | 10 | disputes.py, validation.py | service_not_ready (503) |
| db-gateway | 15 | bank.py, board.py, court.py, identity.py, reputation.py | service_not_ready (503) |
| observatory | 0 (+4 details=None) | events.py | — |
| ui | 0 | — | — |
| **Total** | **59** | **14 files** | |
