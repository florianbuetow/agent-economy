# Gateway-Only Storage Enforcement

**Ticket**: agent-economy-c90l
**Goal**: Remove all non-gateway persistent storage from core services and enforce via architecture tests, dependency checks, and semgrep rules that only the DB Gateway service may use SQLite/aiosqlite.

## Context

All core services (identity, central-bank, task-board, court, reputation) have already migrated their write paths to use Gateway*Store implementations that communicate with the DB Gateway service via HTTP. However, legacy fallback code still exists:

- Each service's `lifespan.py` has an `if settings.db_gateway is not None: ... else: <fallback>` pattern
- In-memory/SQLite store implementations remain in the codebase
- The semgrep rule `no-direct-sql.yml` exempts `reputation_service/services/sqlite_feedback_store.py`
- Reputation's `DuplicateFeedbackError` exception class lives inside `sqlite_feedback_store.py` and is imported by other modules

## Important Rules

- Read `AGENTS.md` before starting any work
- Use `uv run` for all Python execution - never use raw python, python3, or pip install
- Do NOT modify existing test files - add new test files only
- After EACH phase, run `just ci-quiet` from the service directory to verify
- After ALL phases, run `just ci-quiet` from the project root

---

## Phase 1: Reputation Service - Extract DuplicateFeedbackError

The `DuplicateFeedbackError` exception is defined in `sqlite_feedback_store.py` but imported by:
- `services/reputation/src/reputation_service/services/feedback.py`
- `services/reputation/src/reputation_service/services/feedback_store.py`
- `services/reputation/src/reputation_service/services/gateway_feedback_store.py`

### Tasks

1. **Move `DuplicateFeedbackError` to a new file** `services/reputation/src/reputation_service/services/exceptions.py`:
   ```python
   """Domain exceptions for the reputation service."""

   class DuplicateFeedbackError(Exception):
       """Raised when duplicate feedback is submitted."""
   ```

2. **Update all imports** that reference `DuplicateFeedbackError` from `sqlite_feedback_store`:
   - `services/reputation/src/reputation_service/services/feedback.py` - change import to `from reputation_service.services.exceptions import DuplicateFeedbackError`
   - `services/reputation/src/reputation_service/services/gateway_feedback_store.py` - change import to `from reputation_service.services.exceptions import DuplicateFeedbackError`
   - `services/reputation/src/reputation_service/services/feedback_store.py` - change import to `from reputation_service.services.exceptions import DuplicateFeedbackError`
   - `services/reputation/src/reputation_service/core/lifespan.py` - will be updated in Phase 3
   - Also update `services/reputation/src/reputation_service/services/sqlite_feedback_store.py` itself to import from `exceptions.py` instead of defining the class inline (keep backward compatibility for now)

3. **Verify**: `cd services/reputation && just ci-quiet`

---

## Phase 2: Remove In-Memory and SQLite Store Files

Delete the following files (they are legacy fallback implementations):

### Identity Service
- Delete: `services/identity/src/identity_service/services/agent_store.py`
  - Contains `InMemoryAgentStore`, `AgentStore` (sync compat wrapper), `DuplicateAgentError`
  - First check: `DuplicateAgentError` may be imported elsewhere. Search for it.
  - If `DuplicateAgentError` is imported from `agent_store`, move it to a new `services/identity/src/identity_service/services/exceptions.py` file and update all imports.

### Central Bank Service
- Delete: `services/central-bank/src/central_bank_service/services/in_memory_ledger_store.py`
  - Check what it exports and whether anything imports from it besides `lifespan.py` and `services/__init__.py`
  - Update `services/central-bank/src/central_bank_service/services/__init__.py` to remove the import

### Task Board Service
- Delete: `services/task-board/src/task_board_service/services/in_memory_task_store.py`
  - Check imports - `task_store.py` and `lifespan.py` import it
  - Update `services/task-board/src/task_board_service/services/task_store.py` to remove the import

### Court Service
- Delete: `services/court/src/court_service/services/in_memory_dispute_store.py`
  - Check imports - `dispute_store.py` and `lifespan.py` import it
  - Update `services/court/src/court_service/services/dispute_store.py` to remove the import

### Reputation Service
- Delete: `services/reputation/src/reputation_service/services/sqlite_feedback_store.py`
  - By Phase 1, `DuplicateFeedbackError` is already extracted
  - Update `services/reputation/src/reputation_service/services/feedback_store.py` to remove references to `SqliteFeedbackStore`
- Delete: `services/reputation/src/reputation_service/services/feedback_store.py` (it's just a backward-compat shim)
  - Check if anything imports from `feedback_store` first. If so, update those imports to use `gateway_feedback_store` directly.

### Important

Before deleting each file:
1. Search the entire `services/<service>/src/` directory for imports of the file you're about to delete
2. Search the entire `services/<service>/tests/` directory too - note any test files that import these stores
3. For test files: do NOT delete or modify them. If tests import deleted stores, they will fail - that's expected and will be addressed by adding new test files or updating conftest.py (see Phase 4)
4. Update any `__init__.py` files that re-export deleted symbols

### Verify after each service
- `cd services/identity && just ci-quiet`
- `cd services/central-bank && just ci-quiet`
- `cd services/task-board && just ci-quiet`
- `cd services/court && just ci-quiet`
- `cd services/reputation && just ci-quiet`

NOTE: Some tests may fail at this point because they import deleted stores. That is expected. The CI checks for formatting, linting, and type checking should still pass. If tests fail, note which ones and we'll address them in Phase 4.

---

## Phase 3: Update Lifespan Files - Gateway-Only

Remove the fallback `else` branches from all lifespan files so services ONLY use gateway stores. If `db_gateway` config is missing, the service should fail at startup.

### Identity Service (`services/identity/src/identity_service/core/lifespan.py`)
- Remove import of `InMemoryAgentStore` (from `agent_store`)
- Remove the `else` branch that creates `InMemoryAgentStore`
- Make `db_gateway` required: if `settings.db_gateway is None`, raise `RuntimeError("db_gateway configuration is required")`
- Always create `GatewayAgentStore`

### Central Bank Service (`services/central-bank/src/central_bank_service/core/lifespan.py`)
- Remove import of `InMemoryLedgerStore`
- Remove the `else` branch
- Make `db_gateway` required: raise `RuntimeError` if None
- Always create `GatewayLedgerStore`

### Task Board Service (`services/task-board/src/task_board_service/core/lifespan.py`)
- Remove import of `InMemoryTaskStore`
- Remove the `else` branch
- Make `db_gateway` required: raise `RuntimeError` if None
- Always create `GatewayTaskStore`

### Court Service (`services/court/src/court_service/core/lifespan.py`)
- Remove import of `InMemoryDisputeStore`
- Remove the `else` branch
- Make `db_gateway` required: raise `RuntimeError` if None
- Always create `GatewayDisputeStore`

### Reputation Service (`services/reputation/src/reputation_service/core/lifespan.py`)
- Remove import of `SqliteFeedbackStore`
- Remove the `else` branch
- Make `db_gateway` required: raise `RuntimeError` if None
- Always create `GatewayFeedbackStore`

### Config Updates

For each service, check the config model (e.g., `config.py`) to see if `db_gateway` is currently optional. If it has a type like `DbGatewaySettings | None`, consider whether it should remain optional (for test configs) or become required. Keep it optional in the Pydantic model but enforce at startup in lifespan.

### Verify after each service
Run `just ci-quiet` from each service directory. Tests that relied on configs without `db_gateway` will need their config fixtures updated - see Phase 4.

---

## Phase 4: Fix Broken Tests

After Phases 2 and 3, some tests will fail because:
1. They import deleted store files (`agent_store`, `in_memory_*_store`, `sqlite_feedback_store`)
2. They use config fixtures without `db_gateway` settings

**IMPORTANT: Do NOT modify existing test files.** Instead:

### For each service, check which tests fail by running `just test` from the service directory.

### Strategy for fixing tests

The unit tests that use in-memory stores for the store layer itself (e.g., `test_agent_store.py`) should be left alone - they test code that no longer exists, so they should be removed. But since we can't modify existing tests, here's the approach:

**Actually, re-read the AGENTS.md rule**: "Tests are acceptance tests - do NOT modify existing test files." This means:
- If a test file imports a deleted module, it will fail with an `ImportError`
- We need to provide backward-compatibility stubs OR accept that these specific test files are now dead code

**Preferred approach**: Create minimal compatibility shims that re-export from gateway stores or raise clear errors. For each deleted store file, create a minimal stub at the same path that:
- For `agent_store.py`: Keep `InMemoryAgentStore` as an alias for a mock or raise `ImportError` with a clear message. Actually, since the tests for `agent_store` test in-memory behavior that no longer makes sense, we should check if the tests are in the test spec. If they're not acceptance tests for the service's API, they can be considered obsolete.

**Simplest approach**: Keep the in-memory store FILES but remove them from production code paths (lifespan.py). The architecture tests (Phase 5) will enforce that production code never imports them. This way existing tests keep working.

**REVISED APPROACH - DO THIS INSTEAD**:
1. Do NOT delete the in-memory/SQLite store files
2. Only remove their imports from `lifespan.py` files (Phase 3)
3. The architecture tests (Phase 5) will enforce that no production code (i.e., code under `src/` excluding `tests/`) imports these stores
4. This keeps backward compatibility with existing tests while preventing production use

So **skip Phase 2 entirely** and go straight from Phase 1 to Phase 3. The store files stay in the codebase but are dead code in production paths.

### Verify
- Run `just test` from each service directory
- All tests should pass

---

## Phase 5: Architecture Tests - No SQLite/aiosqlite in Non-Gateway Services

Add architecture tests to each core service that verify:
1. No production source file imports `sqlite3` or `aiosqlite`
2. No production source file imports in-memory store modules
3. The `lifespan.py` only imports gateway store, never in-memory/SQLite stores

### For each service, create a NEW test file: `tests/architecture/test_no_direct_db.py`

Use this template (adapt per service):

```python
"""Architecture tests: no direct database access in production code."""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

# Service-specific constants - CHANGE THESE PER SERVICE
_SERVICE_PACKAGE = "identity_service"  # Change per service
_FORBIDDEN_MODULES = frozenset({"sqlite3", "aiosqlite"})
_FORBIDDEN_STORE_MODULES = frozenset({"agent_store", "in_memory_agent_store"})  # Change per service

_SERVICE_SRC = Path(__file__).resolve().parents[2] / "src" / _SERVICE_PACKAGE


def _iter_production_files() -> list[Path]:
    """Return all .py files under src/, excluding __pycache__."""
    return [p for p in _SERVICE_SRC.rglob("*.py") if "__pycache__" not in p.parts]


def _extract_imported_modules(filepath: Path) -> set[str]:
    """Extract all imported module names from a Python file."""
    try:
        tree = ast.parse(filepath.read_text(encoding="utf-8"))
    except SyntaxError:
        return set()

    modules: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                modules.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom) and node.module is not None:
            parts = node.module.split(".")
            modules.add(parts[0])
            # Also track the specific submodule for store checks
            if len(parts) > 1:
                modules.add(parts[-1])
    return modules


@pytest.mark.architecture
class TestNoDirectDatabaseAccess:
    """Ensure no production code directly imports database libraries."""

    def test_no_sqlite3_import(self) -> None:
        """No production source file may import sqlite3."""
        violations = []
        for py_file in _iter_production_files():
            imported = _extract_imported_modules(py_file)
            if "sqlite3" in imported:
                rel = py_file.relative_to(_SERVICE_SRC)
                violations.append(str(rel))
        assert violations == [], (
            f"These files import sqlite3 but must not: {violations}. "
            "All database access must go through the DB Gateway service."
        )

    def test_no_aiosqlite_import(self) -> None:
        """No production source file may import aiosqlite."""
        violations = []
        for py_file in _iter_production_files():
            imported = _extract_imported_modules(py_file)
            if "aiosqlite" in imported:
                rel = py_file.relative_to(_SERVICE_SRC)
                violations.append(str(rel))
        assert violations == [], (
            f"These files import aiosqlite but must not: {violations}. "
            "All database access must go through the DB Gateway service."
        )

    def test_lifespan_does_not_import_legacy_stores(self) -> None:
        """The lifespan module must not import any legacy store implementation."""
        lifespan_path = _SERVICE_SRC / "core" / "lifespan.py"
        if not lifespan_path.exists():
            pytest.skip("No lifespan.py found")
        imported = _extract_imported_modules(lifespan_path)
        violations = imported & _FORBIDDEN_STORE_MODULES
        assert violations == set(), (
            f"lifespan.py imports legacy store modules: {violations}. "
            "Only Gateway*Store should be used in production."
        )
```

### Per-service constants

**Identity** (`_SERVICE_PACKAGE = "identity_service"`):
- `_FORBIDDEN_STORE_MODULES = frozenset({"agent_store", "in_memory_agent_store"})`

**Central Bank** (`_SERVICE_PACKAGE = "central_bank_service"`):
- `_FORBIDDEN_STORE_MODULES = frozenset({"in_memory_ledger_store"})`

**Task Board** (`_SERVICE_PACKAGE = "task_board_service"`):
- `_FORBIDDEN_STORE_MODULES = frozenset({"in_memory_task_store"})`

**Court** (`_SERVICE_PACKAGE = "court_service"`):
- `_FORBIDDEN_STORE_MODULES = frozenset({"in_memory_dispute_store"})`

**Reputation** (`_SERVICE_PACKAGE = "reputation_service"`):
- `_FORBIDDEN_STORE_MODULES = frozenset({"sqlite_feedback_store"})`

### Verify
- `cd services/<service> && just ci-quiet` for each service

---

## Phase 6: Update Semgrep Rule

Update `config/semgrep/no-direct-sql.yml`:

1. Remove the exemption for `src/reputation_service/services/sqlite_feedback_store.py` from ALL four rules
2. The updated `exclude` section for each rule should only contain:
   ```yaml
   paths:
     include:
       - src/
     exclude:
       - tests/
       - src/db_gateway_service/
   ```

### Note on UI and Observatory

The UI and Observatory services use `aiosqlite` for read-only dashboard access. These are infrastructure/dashboard services, not core business services. They are currently not scanned by the semgrep rule because their source directories are outside the `src/` include path when semgrep runs per-service.

However, the semgrep rule's `include: src/` path applies relative to each service. So when run from `services/ui/`, it would check `services/ui/src/`. But UI and Observatory legitimately need aiosqlite for read-only access. If the semgrep check is run per-service from within each service directory, then UI and Observatory WILL be flagged.

**Decision**: Do NOT add UI or Observatory exemptions to the semgrep rule. Instead, verify whether the semgrep check currently runs against UI and Observatory. If it does and they fail, we need to exempt them. If it doesn't run against them, leave as-is.

Check by running: `cd services/ui && just code-semgrep` and `cd services/observatory && just code-semgrep`

If they fail, add exemptions:
```yaml
exclude:
  - tests/
  - src/db_gateway_service/
  - src/ui_service/
  - src/observatory_service/
```

### Verify
- Run `just ci-quiet` from project root

---

## Phase 7: Dependency Check - pyproject.toml Audit

Verify that no core service (identity, central-bank, task-board, court, reputation) lists `sqlite3` or `aiosqlite` as a dependency in their `pyproject.toml`. Note: `sqlite3` is part of the Python stdlib so it won't appear in pyproject.toml, but `aiosqlite` might.

Check each service's `pyproject.toml` for `aiosqlite` - currently none of the core services have it, only UI and Observatory, so this should be a no-op verification.

### Verify
- Grep all pyproject.toml files for sqlite/aiosqlite: `grep -r "sqlite\|aiosqlite" services/*/pyproject.toml`

---

## Phase 8: Final Verification

Run the full CI from the project root:

```bash
just ci-quiet
```

This must pass with zero failures. If any test fails, investigate and fix (without modifying existing test files).

### Summary of expected changes

1. New file: `services/reputation/src/reputation_service/services/exceptions.py` (DuplicateFeedbackError)
2. Modified: `services/reputation/src/reputation_service/services/feedback.py` (import change)
3. Modified: `services/reputation/src/reputation_service/services/gateway_feedback_store.py` (import change)
4. Modified: `services/reputation/src/reputation_service/services/feedback_store.py` (import change)
5. Modified: 5x `core/lifespan.py` files (remove fallback branches, require db_gateway)
6. New files: 5x `tests/architecture/test_no_direct_db.py`
7. Modified: `config/semgrep/no-direct-sql.yml` (remove reputation exemption)

### What is NOT changed
- In-memory store files are NOT deleted (backward compat for existing tests)
- SQLite feedback store file is NOT deleted (backward compat)
- UI and Observatory services are NOT modified (they legitimately use aiosqlite)
- Existing test files are NOT modified
