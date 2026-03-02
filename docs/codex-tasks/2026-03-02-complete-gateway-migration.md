# Complete Gateway Migration — Implementation Plan for Codex

> **Date**: 2026-03-02
> **Agent**: Codex
> **Scope**: All open tickets organized into 4 tiers
> **IMPORTANT**: Do NOT use git. There is no git in this project. Skip all git operations.

## Pre-Flight

Before starting ANY work, read these files IN ORDER:
1. `AGENTS.md` — project conventions, architecture, testing rules
2. `DELEGATE.md` — delegation patterns (for context only)
3. This file (you're reading it now)

Use `uv run` for ALL Python execution. Never use `python`, `python3`, or `pip install`.
Do NOT modify existing test files in `tests/` — add new test files instead.
After EVERY tier, run the CI validation commands specified.

---

## Tier 1: Bug Fixes (3 tickets)

These are independent fixes that can be done in any order.

### 1A. Fix seed script path mismatch (agent-economy-ponf)

**Problem**: `tools/seed-economy.sh` defaults to `data/economy.db` but the DB Gateway reads from `services/db-gateway/data/economy.db`.

**Fix**: Edit `tools/seed-economy.sh` to change the default path.

**File to edit**: `tools/seed-economy.sh`

Find this line (near the top, around line 20-25):
```bash
DB="${1:-data/economy.db}"
```

Change it to:
```bash
DB="${1:-services/db-gateway/data/economy.db}"
```

**Verification**:
```bash
head -25 tools/seed-economy.sh | grep "DB="
# Should show: DB="${1:-services/db-gateway/data/economy.db}"
```

### 1B. Fix _compose_dispute empty escrow_id (agent-economy-0sbi)

**Problem**: `GatewayDisputeStore._compose_dispute()` hardcodes `"escrow_id": ""` because the `court_claims` table has no `escrow_id` column. But the `board_tasks` table does have `escrow_id`. The fix is to look it up from `board_tasks` via the `task_id`.

**File to edit**: `services/court/src/court_service/services/gateway_dispute_store.py`

**Step 1**: Add a method to look up escrow_id from the task board:

After the `_get_ruling` method (around line 60-67), add this new method:

```python
def _get_escrow_id(self, task_id: str) -> str:
    """Look up escrow_id from the board task record."""
    response = self._client.get(f"/board/tasks/{task_id}")
    if response.status_code != 200:
        return ""
    data = self._json(response)
    return str(data.get("escrow_id", ""))
```

**Step 2**: Update `_compose_dispute` to use the new method.

In the `_compose_dispute` method, find this line:
```python
"escrow_id": "",
```

Change it to:
```python
"escrow_id": self._get_escrow_id(str(claim["task_id"])),
```

**Step 3**: Also update `insert_dispute` to pass the escrow_id (it already receives it as a parameter but doesn't use it after filing). No change needed here — the escrow_id is only needed for reading back the dispute. The `_get_escrow_id` lookup handles it.

**Verification**:
```bash
cd services/court && uv run pytest tests/unit/ -x -q 2>&1 | tail -5
```

### 1C. Fix revert_to_rebuttal_pending not deleting ruling/votes (agent-economy-3gzm)

**Problem**: The SQLite `revert_to_rebuttal_pending` deletes votes AND resets status. The gateway version only resets status. It needs to also delete the ruling record (which contains the votes as JSON).

**Step 1**: Add a new DELETE endpoint to the DB Gateway.

**File to create**: No new router file needed. Add to the existing court router.

Edit `services/db-gateway/src/db_gateway_service/routers/court.py`:

Add this endpoint at the end of the file (before the last line):

```python
@router.delete("/rulings/{claim_id}")
async def delete_ruling(claim_id: str) -> JSONResponse:
    """Delete a ruling by claim_id."""
    state = get_app_state()
    if state.db_writer is None:
        raise ServiceError(
            error="service_not_ready",
            message="DbWriter not initialized",
            status_code=503,
            details={},
        )

    result = state.db_writer.delete_ruling(claim_id)
    return JSONResponse(status_code=200, content=result)
```

**Step 2**: Add the `delete_ruling` method to `DbWriter`.

Edit `services/db-gateway/src/db_gateway_service/services/db_writer.py`:

Add this method to the `DbWriter` class (at the end, before `close()`):

```python
def delete_ruling(self, claim_id: str) -> dict[str, object]:
    """Delete a ruling record by claim_id."""
    cursor = self._db.execute(
        "DELETE FROM court_rulings WHERE claim_id = ?",
        (claim_id,),
    )
    self._db.commit()
    return {"deleted": cursor.rowcount > 0, "claim_id": claim_id}
```

**Step 3**: Update `GatewayDisputeStore.revert_to_rebuttal_pending` to also delete the ruling.

Edit `services/court/src/court_service/services/gateway_dispute_store.py`:

Find the `revert_to_rebuttal_pending` method:
```python
def revert_to_rebuttal_pending(self, dispute_id: str) -> None:
    self.set_status(dispute_id, "rebuttal_pending")
```

Replace it with:
```python
def revert_to_rebuttal_pending(self, dispute_id: str) -> None:
    self.set_status(dispute_id, "rebuttal_pending")
    self._delete_ruling(dispute_id)

def _delete_ruling(self, dispute_id: str) -> None:
    """Delete the ruling record for a dispute (removes votes too)."""
    response = self._client.delete(f"/court/rulings/{dispute_id}")
    if response.status_code not in (200, 404):
        msg = f"Gateway error: {response.status_code} {response.text}"
        raise RuntimeError(msg)
```

**Verification**:
```bash
cd services/db-gateway && uv run pytest tests/unit/ -x -q 2>&1 | tail -5
cd services/court && uv run pytest tests/unit/ -x -q 2>&1 | tail -5
```

### Tier 1 Validation

Run full CI for the services changed:
```bash
cd /Users/flo/Developer/github/agent-economy
cd services/db-gateway && just ci-quiet 2>&1 | tail -5
cd ../court && just ci-quiet 2>&1 | tail -5
cd ../.. && just ci-all-quiet 2>&1 | tail -20
```

ALL checks MUST pass before moving to Tier 2.

---

## Tier 2: Architecture Constraint Tests (4 tickets)

These add architecture tests ensuring only Gateway*Store implementations may import httpx or call the db-gateway. One test per service: identity, central-bank, task-board, reputation, court.

**Tickets**: agent-economy-1kbd, agent-economy-2inh, agent-economy-2wmv, agent-economy-9j8f

### Pattern

Each service already has `tests/architecture/test_architecture.py` and `tests/architecture/conftest.py`. The existing tests enforce layer boundaries using `pytestarch`. We need to ADD new test methods (in NEW test files) that verify:

1. Only the `gateway_*_store` modules may import `httpx`
2. No other module in the `services/` package may import `httpx`

### 2A. Identity Service (agent-economy-9j8f — this was filed as reputation, but it applies to all)

Actually wait — looking at the tickets again:
- agent-economy-1kbd = task-board
- agent-economy-2inh = central-bank
- agent-economy-2wmv = court
- agent-economy-9j8f = reputation

We need to create a new architecture test file in EACH of these 4 services (plus identity which doesn't have a ticket but should also be covered). Since we must NOT modify existing test files, we create NEW files.

**For each service, create `tests/architecture/test_gateway_constraint.py`**:

The pattern is the same for all 5 services. Here is the template — substitute the service name and module names:

#### Identity: `services/identity/tests/architecture/test_gateway_constraint.py`

```python
"""Architecture tests: only GatewayAgentStore may import httpx."""

from __future__ import annotations

import ast
import importlib
from pathlib import Path

import pytest


def _get_service_src_dir() -> Path:
    """Return the src directory for this service."""
    return Path(__file__).resolve().parent.parent.parent / "src" / "identity_service"


def _get_python_files(src_dir: Path, exclude_modules: set[str]) -> list[Path]:
    """Get all Python files in src, excluding specific module basenames."""
    files = []
    for py_file in src_dir.rglob("*.py"):
        if py_file.stem in exclude_modules or py_file.name == "__init__.py":
            continue
        files.append(py_file)
    return files


def _file_imports_module(py_file: Path, module_name: str) -> bool:
    """Check if a Python file imports a given module using AST parsing."""
    try:
        tree = ast.parse(py_file.read_text())
    except SyntaxError:
        return False
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name == module_name or alias.name.startswith(f"{module_name}."):
                    return True
        elif isinstance(node, ast.ImportFrom):
            if node.module is not None and (
                node.module == module_name or node.module.startswith(f"{module_name}.")
            ):
                return True
    return False


@pytest.mark.architecture
class TestGatewayConstraint:
    """Only GatewayAgentStore may import httpx."""

    def test_only_gateway_store_imports_httpx(self) -> None:
        """No module besides gateway_agent_store may import httpx."""
        src_dir = _get_service_src_dir()
        allowed = {"gateway_agent_store"}
        violations = []
        for py_file in _get_python_files(src_dir, exclude_modules=allowed):
            if _file_imports_module(py_file, "httpx"):
                rel = py_file.relative_to(src_dir)
                violations.append(str(rel))
        assert violations == [], (
            f"These modules import httpx but should not: {violations}. "
            f"Only gateway_agent_store is allowed to import httpx."
        )
```

#### Central Bank: `services/central-bank/tests/architecture/test_gateway_constraint.py`

Same pattern but:
- src dir: `central_bank_service`
- allowed: `{"gateway_ledger_store"}`
- class docstring: `Only GatewayLedgerStore may import httpx.`
- test docstring: `No module besides gateway_ledger_store may import httpx.`

#### Task Board: `services/task-board/tests/architecture/test_gateway_constraint.py`

Same pattern but:
- src dir: `task_board_service`
- allowed: `{"gateway_task_store"}`
- class docstring: `Only GatewayTaskStore may import httpx.`
- test docstring: `No module besides gateway_task_store may import httpx.`

#### Reputation: `services/reputation/tests/architecture/test_gateway_constraint.py`

Same pattern but:
- src dir: `reputation_service`
- allowed: `{"gateway_feedback_store"}`
- class docstring: `Only GatewayFeedbackStore may import httpx.`
- test docstring: `No module besides gateway_feedback_store may import httpx.`

#### Court: `services/court/tests/architecture/test_gateway_constraint.py`

Same pattern but:
- src dir: `court_service`
- allowed: `{"gateway_dispute_store"}`
- class docstring: `Only GatewayDisputeStore may import httpx.`
- test docstring: `No module besides gateway_dispute_store may import httpx.`

### IMPORTANT NOTES for all 5 files:

1. The `_get_python_files` function must also exclude `gateway_client` since those interim files import httpx too (they'll be removed in Tier 3). So update the `allowed` set to include both:
   - Identity: `{"gateway_agent_store", "gateway_client"}` — wait, identity doesn't have gateway_client. Let me check.

Actually, let me be precise. The interim gateway_client files that exist:
- central-bank: `gateway_client.py` — YES
- task-board: `gateway_client.py` — YES
- reputation: `gateway_client.py` — YES
- court: `gateway_client.py` — YES
- identity: NO gateway_client.py

So the allowed sets should be:
- Identity: `{"gateway_agent_store"}`
- Central Bank: `{"gateway_ledger_store", "gateway_client"}`
- Task Board: `{"gateway_task_store", "gateway_client"}`
- Reputation: `{"gateway_feedback_store", "gateway_client"}`
- Court: `{"gateway_dispute_store", "gateway_client"}`

ALSO: Some `lifespan.py` files import the gateway store modules which in turn import httpx. But lifespan.py doesn't import httpx directly, so this is fine — we only check for direct httpx imports.

Wait — let me think about this more carefully. `gateway_client.py` files themselves import httpx. We want the architecture test to pass NOW (before we remove them in Tier 3). So we must exclude them from the check. After Tier 3 removes them, the exclusion becomes a no-op.

### Tier 2 Validation

Run the architecture tests for each service:
```bash
cd /Users/flo/Developer/github/agent-economy

cd services/identity && uv run pytest tests/architecture/test_gateway_constraint.py -v 2>&1 | tail -10
cd ../central-bank && uv run pytest tests/architecture/test_gateway_constraint.py -v 2>&1 | tail -10
cd ../task-board && uv run pytest tests/architecture/test_gateway_constraint.py -v 2>&1 | tail -10
cd ../reputation && uv run pytest tests/architecture/test_gateway_constraint.py -v 2>&1 | tail -10
cd ../court && uv run pytest tests/architecture/test_gateway_constraint.py -v 2>&1 | tail -10
```

Then run full CI:
```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet 2>&1 | tail -30
```

ALL checks MUST pass before moving to Tier 3.

---

## Tier 3: Remove Local SQLite Databases (agent-economy-tjbh)

This is the largest ticket. Remove all per-service local SQLite store implementations, gateway_client.py files, and local DB initialization.

### CRITICAL RULES:
- Do NOT remove any Protocol/Interface files — only the SQLite implementations
- Do NOT modify existing test files — but you may need to update conftest.py fixtures if they create SQLite stores
- After each service, run `just ci-quiet` to verify

### 3A. Identity Service

**Files to DELETE** (remove entirely):
- `services/identity/src/identity_service/services/agent_store.py` (the SQLite AgentStore — but FIRST check if it's the protocol file)

Wait, let me be precise. The identity service has:
- `agent_store.py` — this might be the protocol or the SQLite impl. Need to check.

Actually, from the exploration: identity has `agent_store.py` (which is likely the old SQLite one) and `gateway_agent_store.py` (the new gateway one). There's also a `protocol.py` in some services.

**READ these files first to understand the structure before deleting anything**:
```bash
head -20 services/identity/src/identity_service/services/agent_store.py
head -20 services/identity/src/identity_service/services/gateway_agent_store.py
ls services/identity/src/identity_service/services/
```

**For EACH service below, you MUST**:
1. Read the service's `services/` directory listing to understand what files exist
2. Read the lifespan.py to understand how the store is instantiated
3. Read each file's first 30 lines to understand what it is (protocol vs SQLite impl vs gateway impl)
4. Only delete files that are confirmed SQLite implementations
5. Update lifespan.py to remove the SQLite fallback path and always use the Gateway store
6. Update config.py to remove `database` config section if it's only used for the SQLite path
7. Run `just ci-quiet` after each service

### Per-Service Removal Instructions

**For each service, follow this exact sequence**:

#### Step 1: Identify files
```bash
ls services/<name>/src/<name>_service/services/
```

#### Step 2: Read and understand each file
Read the first 30 lines of each file to classify:
- Protocol/Interface file → KEEP
- SQLite implementation → MARK FOR DELETION
- Gateway implementation → KEEP
- gateway_client.py (interim) → MARK FOR DELETION

#### Step 3: Update lifespan.py
Edit `services/<name>/src/<name>_service/core/lifespan.py`:
- Remove the `import` of the SQLite store class
- Remove the `import` of gateway_client if present
- Remove the `if settings.db_gateway is not None: ... else: SqliteStore(...)` branch
- Make the gateway store the ONLY path (the db_gateway config is now REQUIRED, not optional)
- Remove any `db_path` variable, `Path(db_path).parent.mkdir(...)`, schema initialization

Example of what the store initialization should look like AFTER:
```python
from <service>_service.services.gateway_<name>_store import Gateway<Name>Store

# In the lifespan function:
store = Gateway<Name>Store(
    base_url=settings.db_gateway.url,
    timeout_seconds=settings.db_gateway.timeout_seconds,
)
state.<store_field> = store
```

#### Step 4: Update config.py
Edit `services/<name>/src/<name>_service/config.py`:
- If `database: DatabaseConfig` is only used for the local SQLite path, change `db_gateway: DbGatewayConfig | None = None` to `db_gateway: DbGatewayConfig` (make it required, not optional)
- Do NOT remove `database` config if other things depend on it — some services may use `database.path` for other purposes. Check carefully.

#### Step 5: Delete SQLite files
Remove the identified SQLite store files and gateway_client.py.

#### Step 6: Update any conftest.py files that create SQLite stores
Check `tests/unit/conftest.py` and `tests/conftest.py` for any fixtures that create the old SQLite store. If they do, update them to create the Gateway store instead (or mock the store interface).

**IMPORTANT**: Do NOT modify existing test files. If a conftest.py creates a fixture used by existing tests, and changing it would break those tests, you need to figure out an alternative. Options:
- If the fixture creates a SQLite store, keep the SQLite store file purely for tests (don't delete it)
- Or mock the store interface in the fixture
- Or skip the deletion of that particular file and leave a TODO comment

#### Step 7: Verify
```bash
cd services/<name> && just ci-quiet 2>&1 | tail -10
```

### Service-Specific Details

#### Identity Service
- SQLite store: `services/identity/src/identity_service/services/agent_store.py` — Check if this is a protocol or implementation. The identity service might use `agent_store.py` as the protocol name. Read it first.
- Gateway store: `services/identity/src/identity_service/services/gateway_agent_store.py`
- No gateway_client.py
- Config: Has `database` and `db_gateway` sections

#### Central Bank Service
- SQLite store: `services/central-bank/src/central_bank_service/services/sqlite_ledger_store.py`
- Gateway store: `services/central-bank/src/central_bank_service/services/gateway_ledger_store.py`
- Interim: `services/central-bank/src/central_bank_service/services/gateway_client.py` — DELETE
- Config: Has `database` and `db_gateway` sections
- Lifespan has extra salary distribution logic — be careful not to remove it

#### Task Board Service
- SQLite store: `services/task-board/src/task_board_service/services/sqlite_task_store.py`
- There's also `task_store.py` — check if it's a protocol
- Gateway store: `services/task-board/src/task_board_service/services/gateway_task_store.py`
- Interim: `services/task-board/src/task_board_service/services/gateway_client.py` — DELETE
- Lifespan is complex (has deadline scheduler, salary distribution) — only remove the SQLite store init, not the other logic

#### Reputation Service
- SQLite store: `services/reputation/src/reputation_service/services/sqlite_feedback_store.py`
- There's also `feedback_store.py` — check if it's a protocol
- Gateway store: `services/reputation/src/reputation_service/services/gateway_feedback_store.py`
- Interim: `services/reputation/src/reputation_service/services/gateway_client.py` — DELETE

#### Court Service
- SQLite store: `services/court/src/court_service/services/sqlite_dispute_store.py`
- There's also `dispute_store.py` — check if it's a protocol
- Gateway store: `services/court/src/court_service/services/gateway_dispute_store.py`
- Interim: `services/court/src/court_service/services/gateway_client.py` — DELETE

### Tier 3 Validation

After ALL services are updated:
```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet 2>&1 | tail -30
```

ALL checks MUST pass. If any service fails, fix it before moving on.

Also verify no service imports sqlite3 anymore (except db-gateway):
```bash
grep -r "import sqlite3\|from sqlite3" services/*/src/ --include="*.py" | grep -v db-gateway | grep -v __pycache__
# Should return nothing
```

---

## Tier 4: Semgrep Rule + Verification (agent-economy-9jly + verification tickets)

### 4A. Create the semgrep rule

**File to create**: `config/semgrep/no-direct-sql.yml`

```yaml
rules:
  - id: no-direct-sql-import
    patterns:
      - pattern: import sqlite3
    message: >
      Direct sqlite3 import is prohibited. All database access must go through
      the DB Gateway service. Use Gateway*Store implementations instead.
    languages: [python]
    severity: ERROR
    paths:
      exclude:
        - "*/db-gateway/*"
        - "*/tests/*"

  - id: no-direct-sql-from-import
    patterns:
      - pattern: from sqlite3 import ...
    message: >
      Direct sqlite3 import is prohibited. All database access must go through
      the DB Gateway service. Use Gateway*Store implementations instead.
    languages: [python]
    severity: ERROR
    paths:
      exclude:
        - "*/db-gateway/*"
        - "*/tests/*"

  - id: no-aiosqlite-import
    patterns:
      - pattern: import aiosqlite
    message: >
      Direct aiosqlite import is prohibited. All database access must go through
      the DB Gateway service.
    languages: [python]
    severity: ERROR
    paths:
      exclude:
        - "*/db-gateway/*"
        - "*/tests/*"

  - id: no-aiosqlite-from-import
    patterns:
      - pattern: from aiosqlite import ...
    message: >
      Direct aiosqlite import is prohibited. All database access must go through
      the DB Gateway service.
    languages: [python]
    severity: ERROR
    paths:
      exclude:
        - "*/db-gateway/*"
        - "*/tests/*"
```

### 4B. Verify semgrep passes for all services

Run semgrep against each service:
```bash
cd /Users/flo/Developer/github/agent-economy

# Check each service
for svc in identity central-bank task-board reputation court observatory ui; do
  echo "=== $svc ==="
  cd services/$svc && uv run semgrep --config ../../config/semgrep/no-direct-sql.yml --error src/ 2>&1 | tail -5
  cd ../..
done
```

### 4C. Verify db-gateway is excluded

```bash
cd services/db-gateway
uv run semgrep --config ../../config/semgrep/no-direct-sql.yml --error src/ 2>&1 | tail -5
# Should pass (db-gateway is excluded)
```

### Tier 4 Validation

```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet 2>&1 | tail -30
```

---

## Final Validation

After ALL tiers are complete, run the FULL project CI:

```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet 2>&1 | tail -30
```

This MUST pass with zero failures. If it doesn't, investigate and fix.

Also run the E2E pipeline if available:
```bash
just start-all 2>&1 | tail -10
just status 2>&1
# If all services are healthy:
just test-all 2>&1 | tail -30
just stop-all
```

---

## Summary of All Files to Create/Edit/Delete

### CREATE:
- `services/identity/tests/architecture/test_gateway_constraint.py`
- `services/central-bank/tests/architecture/test_gateway_constraint.py`
- `services/task-board/tests/architecture/test_gateway_constraint.py`
- `services/reputation/tests/architecture/test_gateway_constraint.py`
- `services/court/tests/architecture/test_gateway_constraint.py`
- `config/semgrep/no-direct-sql.yml`

### EDIT:
- `tools/seed-economy.sh` (fix default path)
- `services/court/src/court_service/services/gateway_dispute_store.py` (fix escrow_id + revert_to_rebuttal_pending)
- `services/db-gateway/src/db_gateway_service/routers/court.py` (add DELETE /rulings/{claim_id})
- `services/db-gateway/src/db_gateway_service/services/db_writer.py` (add delete_ruling method)
- `services/*/src/*/core/lifespan.py` (remove SQLite fallback for 5 services)
- `services/*/src/*/config.py` (make db_gateway required for 5 services)

### DELETE (after verifying not used by existing tests):
- `services/central-bank/src/central_bank_service/services/sqlite_ledger_store.py`
- `services/central-bank/src/central_bank_service/services/gateway_client.py`
- `services/task-board/src/task_board_service/services/sqlite_task_store.py`
- `services/task-board/src/task_board_service/services/gateway_client.py`
- `services/reputation/src/reputation_service/services/sqlite_feedback_store.py`
- `services/reputation/src/reputation_service/services/gateway_client.py`
- `services/court/src/court_service/services/sqlite_dispute_store.py`
- `services/court/src/court_service/services/gateway_client.py`
- `services/identity/src/identity_service/services/agent_store.py` (IF it's the SQLite impl, not a protocol)
