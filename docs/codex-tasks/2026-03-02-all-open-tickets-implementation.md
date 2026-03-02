# All Open Tickets — Complete Tiered Implementation Plan

> **Date:** 2026-03-02
>
> **Tickets covered (9 ready + downstream):**
> - Tier 1 (bugs, no deps): agent-economy-dbh1, agent-economy-hhq9, agent-economy-5yv4
> - Tier 2 (foundational features): agent-economy-zpby, agent-economy-t13s
> - Tier 3 (architecture features): agent-economy-523w, agent-economy-sun2
> - Tier 4 (gateway features): agent-economy-2g1b
> - Tier 5 (architecture constraints): agent-economy-w9y0
>
> **Goal:** Systematically resolve all 9 ready tickets in dependency order, from simple bugfixes through foundational infrastructure to complex features.

## CRITICAL RULES

- **NO GIT.** Do not use any git commands. There is no git in this project. Just edit files directly.
- **Use `uv run` for all Python execution.** NEVER use `python`, `python3`, or `pip install`.
- **Do NOT modify existing test files.** Add new test files for new tests.
- **All Pydantic models use `ConfigDict(extra="forbid")`.**
- **Never hardcode configuration values.** All config comes from `config.yaml`.
- **Never use default parameter values** for configurable settings.
- **Business logic stays in `services/` layer** — routers are thin wrappers.
- Read `AGENTS.md` before starting any work.
- After EACH tier, run the specified verification commands. Do NOT proceed to the next tier until verification passes.

---

# TIER 1: Bug Fixes (No Dependencies)

These three tickets fix existing violations. They are independent and can be done in any order.

---

## Ticket: agent-economy-dbh1 — Fix architecture violations: routers importing config directly

### Problem

Four services have routers that import `get_settings()` directly instead of getting config through AppState. This violates the architecture rule that routers should be thin HTTP translators that get everything through dependency injection or AppState.

### Files with violations

1. **`services/central-bank/src/central_bank_service/routers/helpers.py`** — imports `get_settings()` at line 10
2. **`services/reputation/src/reputation_service/routers/feedback.py`** — imports `get_settings()` at lines 11, 150, 285, 312, 339
3. **`services/court/src/court_service/routers/disputes.py`** — imports `get_settings()` at line 15
4. **`services/observatory/src/observatory_service/routers/events.py`** — imports `get_settings()` at line 9

### Fix Strategy

For each service, the pattern is the same:
1. Identify what config values the router accesses (e.g., `settings.feedback.max_comment_length`)
2. Add those values to the service's `AppState` dataclass in `core/state.py`
3. Initialize them in `core/lifespan.py` from settings
4. In the router, replace `get_settings()` calls with `get_app_state()` lookups

### Detailed Steps

#### Step 1: Central Bank — `services/central-bank/src/central_bank_service/routers/helpers.py`

1. Read the file to understand what settings it accesses
2. Read `services/central-bank/src/central_bank_service/core/state.py` to understand the AppState
3. Read `services/central-bank/src/central_bank_service/core/lifespan.py` to understand initialization
4. Add the needed config fields to AppState (as regular fields, not `| None`)
5. Initialize them in lifespan from `get_settings()`
6. In helpers.py, replace `from central_bank_service.config import get_settings` with `from central_bank_service.core.state import get_app_state`
7. Replace `settings = get_settings()` with `state = get_app_state()` and update field references

#### Step 2: Reputation — `services/reputation/src/reputation_service/routers/feedback.py`

1. Read the file — it uses `get_settings()` multiple times (lines 150, 285, 312, 339)
2. Identify all settings accessed (likely `settings.feedback.max_comment_length`, `settings.feedback.max_rating`, etc.)
3. Add these to Reputation's AppState
4. Initialize in lifespan
5. Replace all `get_settings()` calls in the router with AppState lookups

#### Step 3: Court — `services/court/src/court_service/routers/disputes.py`

1. Read the file to find what settings it accesses (likely `settings.disputes.max_claim_length`, `settings.disputes.max_rebuttal_length`, etc.)
2. Add to Court's AppState
3. Initialize in lifespan
4. Replace imports and calls

#### Step 4: Observatory — `services/observatory/src/observatory_service/routers/events.py`

1. Read the file to find what settings it accesses (likely `settings.sse.batch_size` or similar)
2. Add to Observatory's AppState
3. Initialize in lifespan
4. Replace imports and calls

### Verification

After ALL four fixes:
```bash
cd /Users/flo/Developer/github/agent-economy/services/central-bank && uv run just ci-quiet
cd /Users/flo/Developer/github/agent-economy/services/reputation && uv run just ci-quiet
cd /Users/flo/Developer/github/agent-economy/services/court && uv run just ci-quiet
cd /Users/flo/Developer/github/agent-economy/services/observatory && uv run just ci-quiet
```

Also verify the config import is gone:
```bash
grep -rn "from.*config import get_settings" services/central-bank/src/central_bank_service/routers/
grep -rn "from.*config import get_settings" services/reputation/src/reputation_service/routers/
grep -rn "from.*config import get_settings" services/court/src/court_service/routers/
grep -rn "from.*config import get_settings" services/observatory/src/observatory_service/routers/
```

All four grep commands should return NO output (no matches).

### What NOT to do
- Do NOT change any business logic — only move config access from routers to AppState
- Do NOT change test files
- Do NOT add default values to config fields
- Do NOT change config.yaml files

---

## Ticket: agent-economy-hhq9 — Fix architecture violations: reputation services layer accessing core

### Problem

The reputation service's `services/` layer imports from `core/`. Business logic in `services/` should not import from `core/`.

### Investigation

Read these files:
- `services/reputation/src/reputation_service/services/feedback_store.py`
- `services/reputation/src/reputation_service/services/feedback.py`
- `services/reputation/src/reputation_service/core/state.py`

Look for imports like:
```python
from reputation_service.core.state import FeedbackRecord
```

### Fix Strategy

The `FeedbackRecord` dataclass is a data structure used by both core and services layers. It should live in a neutral location — either `schemas.py` (if it's a Pydantic model) or a new `types.py` / `models.py` file in the service root package (not in `core/` or `services/`).

1. Read the `FeedbackRecord` definition in `core/state.py` to understand what it is
2. If it's a plain dataclass used as a return type, move it to `services/reputation/src/reputation_service/schemas.py` or create a `types.py` at the package root
3. Update all imports in `core/state.py`, `services/feedback_store.py`, and any other files that reference it
4. The key rule: `services/` must NOT import from `core/`. `core/` MAY import from `services/` or shared modules like `schemas.py`

### Verification
```bash
cd /Users/flo/Developer/github/agent-economy/services/reputation && uv run just ci-quiet
```

Also verify:
```bash
grep -rn "from reputation_service.core" services/reputation/src/reputation_service/services/
```
Should return NO output.

---

## Ticket: agent-economy-5yv4 — Fix observatory middleware reference and pre-existing CI failures

### Problem

1. Observatory has no `core/middleware.py` but an architecture test may reference it
2. Observatory CI fails due to pre-existing lint/format issues in `data/seed.py` and `data/simulate.py`

### Investigation

1. Read `services/observatory/tests/` to find any architecture tests
2. Check if there's a test that checks for middleware imports
3. Read `services/observatory/src/observatory_service/` directory structure
4. Read `services/observatory/data/seed.py` and `data/simulate.py` for lint issues

### Fix Strategy

#### Part A: Middleware reference
- If an architecture test references middleware.py that doesn't exist, the test configuration needs updating
- Observatory may not need middleware at all — check if other services' middleware pattern is relevant
- If the test is in a shared architecture test file, it may need an exception for observatory

#### Part B: CI failures in data/ scripts
- Read `services/observatory/data/seed.py` and `services/observatory/data/simulate.py`
- Run `cd services/observatory && uv run just code-format` to auto-fix formatting
- Run `cd services/observatory && uv run just code-style` to check remaining issues
- Fix any remaining lint issues manually (unused imports, type annotations, etc.)
- These are standalone scripts, not part of the service package, so they may need different handling

### Verification
```bash
cd /Users/flo/Developer/github/agent-economy/services/observatory && uv run just ci-quiet
```

---

## TIER 1 VERIFICATION — Run After All Three Bug Fixes

```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet
```

This runs the full CI pipeline for ALL services. Every service must pass. If any service fails, fix it before proceeding to Tier 2.

**STOP HERE. Do not proceed to Tier 2 until `just ci-all-quiet` passes cleanly.**

---

# TIER 2: Foundational Infrastructure (Unblocks Downstream)

These tickets create infrastructure that other tickets depend on.

---

## Ticket: agent-economy-zpby — Fix schema divergences between service DBs and economy.db

### Problem

The DB Gateway's unified schema (`docs/specifications/schema.sql`) is missing columns that services need. These must be added before services can migrate to using the gateway as their sole data store.

### Schema Changes Required

Read the current schema first:
```
docs/specifications/schema.sql
```

Then make these additions:

#### 1. `board_bids` — add `amount` column
```sql
-- BEFORE:
CREATE TABLE board_bids (
    bid_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES board_tasks(task_id),
    bidder_id TEXT NOT NULL REFERENCES identity_agents(agent_id),
    proposal TEXT NOT NULL,
    submitted_at TEXT NOT NULL
);

-- AFTER: add amount INTEGER after proposal
CREATE TABLE board_bids (
    bid_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL REFERENCES board_tasks(task_id),
    bidder_id TEXT NOT NULL REFERENCES identity_agents(agent_id),
    proposal TEXT NOT NULL,
    amount INTEGER NOT NULL,
    submitted_at TEXT NOT NULL
);
```

#### 2. `board_assets` — add `content_hash` column
```sql
-- Add content_hash TEXT after storage_path:
content_hash TEXT,
```

#### 3. `board_tasks` — add `bid_count` and `escrow_pending`
```sql
-- Add after existing columns (before created_at or at end of column list):
bid_count INTEGER NOT NULL DEFAULT 0,
escrow_pending INTEGER NOT NULL DEFAULT 0,
```

#### 4. `court_claims` — add `rebuttal_deadline`
```sql
-- Add after status column:
rebuttal_deadline TEXT,
```

#### 5. `board_tasks` — verify deadline columns exist
Verify these columns are present in board_tasks:
- `bidding_deadline TEXT`
- `execution_deadline TEXT`
- `review_deadline TEXT`
- `bidding_deadline_seconds INTEGER`
- `deadline_seconds INTEGER`
- `review_deadline_seconds INTEGER`

If any are missing, add them.

### DB Gateway Code Changes

After updating schema.sql, update the DB Gateway service code:

#### File: `services/db-gateway/src/db_gateway_service/routers/board.py`

1. In the `POST /board/bids` endpoint: add `amount` to required fields validation
2. In the `POST /board/assets` endpoint: add `content_hash` as an optional field (do not require it)

#### File: `services/db-gateway/src/db_gateway_service/services/db_writer.py`

1. In `submit_bid()`: include `amount` in the INSERT statement
2. In `record_asset()`: include `content_hash` in the INSERT statement (allow NULL)
3. In `create_task()`: include `bid_count` and `escrow_pending` in the INSERT (use 0 as the explicit value passed in data)

#### File: `services/db-gateway/src/db_gateway_service/routers/court.py`

1. In `POST /court/claims`: add `rebuttal_deadline` as optional field, pass to service

#### File: `services/db-gateway/src/db_gateway_service/services/db_writer.py`

1. In `file_claim()`: include `rebuttal_deadline` in the INSERT (allow NULL)

### Test Updates

Add new test files (do NOT modify existing tests):

#### File: `services/db-gateway/tests/unit/test_schema_additions.py`

```python
"""Tests for schema additions: board_bids.amount, board_assets.content_hash, etc."""
import pytest

@pytest.mark.unit
class TestSchemaAdditions:

    def test_submit_bid_with_amount(self, app_with_writer):
        """board_bids now accepts amount field."""
        client = app_with_writer
        # First register an agent and create a task
        # Then submit a bid with amount field
        # Assert 201 and amount stored

    def test_submit_bid_requires_amount(self, app_with_writer):
        """amount is required for bid submission."""
        client = app_with_writer
        # Submit bid without amount → 400

    def test_record_asset_with_content_hash(self, app_with_writer):
        """board_assets accepts optional content_hash."""
        client = app_with_writer
        # Record asset with content_hash → 201

    def test_record_asset_without_content_hash(self, app_with_writer):
        """board_assets works without content_hash (nullable)."""
        client = app_with_writer
        # Record asset without content_hash → 201

    def test_create_task_with_bid_count(self, app_with_writer):
        """board_tasks accepts bid_count and escrow_pending."""
        # ...

    def test_file_claim_with_rebuttal_deadline(self, app_with_writer):
        """court_claims accepts rebuttal_deadline."""
        # ...
```

Use the existing test fixtures from `services/db-gateway/tests/unit/conftest.py` — specifically the `app_with_writer` fixture that provides a test client with an initialized database.

### Verification
```bash
cd /Users/flo/Developer/github/agent-economy/services/db-gateway && uv run just ci-quiet
```

---

## Ticket: agent-economy-t13s — Write acceptance tests: semgrep rule blocks direct SQL imports

### Problem

Need tests proving the semgrep no-direct-sql rule works before the rule itself is created.

### Implementation

#### Step 1: Create test fixture directory
```bash
mkdir -p config/semgrep/tests/no-direct-sql/
```

#### Step 2: Create test fixture files

**File: `config/semgrep/tests/no-direct-sql/bad_sqlite3_import.py`**
```python
# ruleid: no-direct-sql
import sqlite3

conn = sqlite3.connect(":memory:")
```

**File: `config/semgrep/tests/no-direct-sql/bad_aiosqlite_import.py`**
```python
# ruleid: no-direct-sql
import aiosqlite

db = aiosqlite.connect(":memory:")
```

**File: `config/semgrep/tests/no-direct-sql/bad_from_import.py`**
```python
# ruleid: no-direct-sql
from sqlite3 import connect

conn = connect(":memory:")
```

**File: `config/semgrep/tests/no-direct-sql/ok_no_sql.py`**
```python
# ok: no-direct-sql
import json
import os

data = json.loads("{}")
```

#### Step 3: Understand semgrep test conventions

Read existing semgrep rules in `config/semgrep/` to understand the format. Semgrep test files use comments like `# ruleid: <rule-id>` on lines that SHOULD match and `# ok: <rule-id>` on lines that should NOT match.

The semgrep test runner (`semgrep --test`) automatically finds test files next to rule files. So the test fixtures should be structured according to semgrep's test conventions.

#### Step 4: Verify test structure is correct

Check what the existing semgrep test structure looks like:
```bash
ls -la config/semgrep/
find config/semgrep/ -name "*.py"
```

Match the existing convention exactly.

### Important Notes

- These tests are expected to FAIL until the rule itself is created (ticket agent-economy-9jly)
- The fixture files must be syntactically valid Python
- The test proves the rule catches violations — it does NOT create the rule
- DB Gateway should be exempt from the rule — create an `ok_db_gateway.py` fixture if the rule supports path-based exclusions

### Verification

Since the rule doesn't exist yet, you cannot run `semgrep --test`. Instead verify:
1. All fixture files are valid Python: `uv run python -c "import py_compile; py_compile.compile('config/semgrep/tests/no-direct-sql/bad_sqlite3_import.py')"` for each file
2. The directory structure matches semgrep test conventions

---

## TIER 2 VERIFICATION

```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet
```

**STOP HERE. Do not proceed to Tier 3 until `just ci-all-quiet` passes cleanly.**

---

# TIER 3: Architecture Features (Store Protocols + Shared HTTP Client)

These can be done in parallel on two different tmux sessions.

---

## Ticket: agent-economy-523w — Define Store Protocol interfaces for all services

### Overview

Create a Python `Protocol` (from `typing`) for each service's data access layer. The identity service already has this done — use it as the reference implementation.

### Reference Implementation

Read these files first:
```
services/identity/src/identity_service/services/protocol.py
services/identity/src/identity_service/services/sqlite_agent_store.py
services/identity/src/identity_service/services/gateway_agent_store.py
services/identity/src/identity_service/core/state.py
```

The identity service shows the complete pattern:
1. `protocol.py` defines `IdentityStorageInterface` (a Protocol)
2. `sqlite_agent_store.py` implements `SqliteAgentStore` (local SQLite)
3. `gateway_agent_store.py` implements `GatewayAgentStore` (HTTP to DB Gateway)
4. `state.py` types the store field as the Protocol interface

### Per-Service Implementation

#### Central Bank: `services/central-bank/`

1. Read `services/central-bank/src/central_bank_service/services/ledger.py` — the current `Ledger` class
2. Create `services/central-bank/src/central_bank_service/services/protocol.py`:

```python
"""Storage protocol for Central Bank service."""
from __future__ import annotations

from typing import Protocol


class LedgerStore(Protocol):
    """Protocol defining the Central Bank storage interface."""

    def create_account(self, account_id: str, initial_balance: int) -> dict: ...
    def get_account(self, account_id: str) -> dict | None: ...
    def credit(self, account_id: str, amount: int, reference: str) -> dict: ...
    def get_transactions(self, account_id: str) -> list[dict]: ...
    def escrow_lock(self, payer_account_id: str, amount: int, task_id: str) -> dict: ...
    def escrow_release(self, escrow_id: str, recipient_account_id: str) -> dict: ...
    def escrow_split(self, escrow_id: str, worker_account_id: str, worker_pct: int, poster_account_id: str) -> dict: ...
    def count_accounts(self) -> int: ...
    def total_escrowed(self) -> int: ...
```

3. Rename `ledger.py` to `sqlite_ledger_store.py`
4. Rename the class from `Ledger` to `SqliteLedgerStore`
5. Update ALL imports across the entire central-bank service:
   - `core/state.py` — change type annotation to `LedgerStore` (Protocol)
   - `core/lifespan.py` — change import to `SqliteLedgerStore`
   - `routers/*.py` — if they reference `Ledger`, update to `LedgerStore`
   - `services/*.py` — update any internal references
6. Verify method signatures match between Protocol and SqliteLedgerStore

**Finding all imports to update:**
```bash
grep -rn "from.*ledger import\|import.*Ledger\|ledger.Ledger" services/central-bank/src/
```

#### Task Board: `services/task-board/`

1. Read `services/task-board/src/task_board_service/services/task_store.py` — the current `TaskStore` class
2. Read `services/task-board/src/task_board_service/services/asset_manager.py` — for `AssetManager`
3. Create `services/task-board/src/task_board_service/services/protocol.py`:

```python
"""Storage protocols for Task Board service."""
from __future__ import annotations

from typing import Protocol


class TaskStoreProtocol(Protocol):
    """Protocol defining the Task Board task storage interface."""

    def insert_task(self, task_data: dict) -> dict: ...
    def get_task(self, task_id: str) -> dict | None: ...
    def list_tasks(self, status: str | None, poster_id: str | None, worker_id: str | None, limit: int, offset: int) -> list[dict]: ...
    def update_task(self, task_id: str, updates: dict) -> dict: ...
    def insert_bid(self, bid_data: dict) -> dict: ...
    def get_bid(self, bid_id: str, task_id: str) -> dict | None: ...
    def get_bids_for_task(self, task_id: str) -> list[dict]: ...
    def count_tasks(self) -> int: ...
    def count_tasks_by_status(self) -> dict: ...


class AssetStoreProtocol(Protocol):
    """Protocol defining the Task Board asset storage interface."""

    def insert_asset(self, asset_data: dict) -> dict: ...
    def get_asset(self, asset_id: str, task_id: str) -> dict | None: ...
    def get_assets_for_task(self, task_id: str) -> list[dict]: ...
    def count_assets(self, task_id: str) -> int: ...
```

4. Rename `task_store.py` to `sqlite_task_store.py`, class to `SqliteTaskStore`
5. Extract asset DB methods from `AssetManager` into `sqlite_asset_store.py` as `SqliteAssetStore` (or keep AssetManager but create the Protocol for future use)
6. Update ALL imports across the task-board service

**IMPORTANT:** The `TaskStore` name is already used as the class name. When creating the Protocol, use `TaskStoreProtocol` to avoid name collision, OR rename the concrete class to `SqliteTaskStore` and keep `TaskStore` as an alias or use `TaskStoreProtocol`.

Actually, looking at the identity service pattern: the Protocol is `IdentityStorageInterface`, the SQLite impl is `SqliteAgentStore`. So follow the same pattern:
- Protocol: `TaskStorageInterface`
- SQLite: `SqliteTaskStore` (rename from `TaskStore`)
- Protocol: `AssetStorageInterface`
- SQLite: `SqliteAssetStore` (extract from `AssetManager`)

**Finding all imports to update:**
```bash
grep -rn "from.*task_store import\|import.*TaskStore\|task_store.TaskStore" services/task-board/src/
grep -rn "from.*asset_manager import\|import.*AssetManager" services/task-board/src/
```

#### Reputation: `services/reputation/`

1. Read `services/reputation/src/reputation_service/services/feedback_store.py` — current `FeedbackStore`
2. Create `services/reputation/src/reputation_service/services/protocol.py`:

```python
"""Storage protocol for Reputation service."""
from __future__ import annotations

from typing import Protocol


class FeedbackStorageInterface(Protocol):
    """Protocol defining the Reputation storage interface."""

    def insert_feedback(self, task_id: str, from_agent_id: str, to_agent_id: str, category: str, rating: str, comment: str | None, *, force_visible: bool) -> dict: ...
    def get_by_id(self, feedback_id: str) -> dict | None: ...
    def get_by_task(self, task_id: str) -> list[dict]: ...
    def get_by_agent(self, agent_id: str) -> list[dict]: ...
    def count(self) -> int: ...
```

3. Rename `feedback_store.py` to `sqlite_feedback_store.py`, class to `SqliteFeedbackStore`
4. Update ALL imports

**Finding all imports to update:**
```bash
grep -rn "from.*feedback_store import\|import.*FeedbackStore\|feedback_store.FeedbackStore" services/reputation/src/
```

#### Court: `services/court/`

1. Read `services/court/src/court_service/services/dispute_store.py` — current `DisputeStore`
2. Create `services/court/src/court_service/services/protocol.py`:

```python
"""Storage protocol for Court service."""
from __future__ import annotations

from typing import Protocol


class DisputeStorageInterface(Protocol):
    """Protocol defining the Court storage interface."""

    def insert_dispute(self, task_id: str, claimant_id: str, respondent_id: str, claim: str, escrow_id: str, rebuttal_deadline: str) -> dict: ...
    def get_dispute(self, dispute_id: str) -> dict | None: ...
    def list_disputes(self, task_id: str | None, status: str | None) -> list[dict]: ...
    def update_rebuttal(self, dispute_id: str, rebuttal: str) -> dict: ...
    def set_status(self, dispute_id: str, status: str) -> None: ...
    def persist_ruling(self, dispute_id: str, worker_pct: int, ruling_summary: str, votes: list[dict]) -> dict: ...
    def revert_to_rebuttal_pending(self, dispute_id: str) -> None: ...
    def count_disputes(self) -> int: ...
    def count_active(self) -> int: ...
```

3. Rename `dispute_store.py` to `sqlite_dispute_store.py`, class to `SqliteDisputeStore`
4. Update ALL imports

**Finding all imports to update:**
```bash
grep -rn "from.*dispute_store import\|import.*DisputeStore\|dispute_store.DisputeStore" services/court/src/
```

### Critical Rules for Protocol Refactor

1. **Do NOT change any method signatures** — the Protocol must match the existing implementation exactly
2. **Do NOT change any business logic** — this is purely a rename + type annotation change
3. **Read EVERY method of each store class** to ensure the Protocol has the exact same signatures
4. **The Protocol must use `from __future__ import annotations`** for forward references
5. **AppState should type the field as the Protocol** (e.g., `store: LedgerStore | None = None`)
6. **Lifespan should import and instantiate the concrete class** (e.g., `SqliteLedgerStore(...)`)
7. **Routers and business logic should only reference the Protocol type** (if they reference it at all)

### Verification

After ALL four services are updated:
```bash
cd /Users/flo/Developer/github/agent-economy/services/central-bank && uv run just ci-quiet
cd /Users/flo/Developer/github/agent-economy/services/task-board && uv run just ci-quiet
cd /Users/flo/Developer/github/agent-economy/services/reputation && uv run just ci-quiet
cd /Users/flo/Developer/github/agent-economy/services/court && uv run just ci-quiet
```

Then full project:
```bash
cd /Users/flo/Developer/github/agent-economy && just ci-all-quiet
```

---

## Ticket: agent-economy-sun2 — Create shared HTTP client library in db-gateway service

### Overview

Create a shared client library that provides typed HTTP clients for inter-service communication. Currently each service has its own `IdentityClient`, `GatewayClient`, `CentralBankClient` implementations with duplicated error handling, timeout logic, and lifecycle management.

### Design

The library lives in a new `libs/service-clients/` package (similar to `libs/service-commons/`).

**Package structure:**
```
libs/service-clients/
├── pyproject.toml
└── src/service_clients/
    ├── __init__.py
    ├── base.py              # BaseServiceClient
    ├── identity.py          # IdentityClient
    ├── bank.py              # BankClient
    ├── task_board.py        # TaskBoardClient
    ├── reputation.py        # ReputationClient
    ├── court.py             # CourtClient
    └── gateway.py           # GatewayClient (DB Gateway)
```

### Step-by-Step Implementation

#### Step 1: Create package structure

```bash
mkdir -p libs/service-clients/src/service_clients
```

#### Step 2: Create `libs/service-clients/pyproject.toml`

Read `libs/service-commons/pyproject.toml` as reference, then create:

```toml
[project]
name = "service-clients"
version = "0.1.0"
description = "Shared HTTP client library for inter-service communication"
requires-python = ">=3.12"
dependencies = [
    "httpx>=0.27.0",
    "service-commons",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/service_clients"]
```

#### Step 3: Create `libs/service-clients/src/service_clients/__init__.py`

```python
"""Shared HTTP client library for inter-service communication."""
__version__ = "0.1.0"
```

#### Step 4: Create `libs/service-clients/src/service_clients/base.py`

```python
"""Base HTTP client with shared lifecycle, timeout, and error handling."""
from __future__ import annotations

import httpx
from service_commons.exceptions import ServiceError


class BaseServiceClient:
    """Base class for all inter-service HTTP clients.

    Provides shared httpx.AsyncClient lifecycle management,
    timeout configuration, and consistent error handling.
    """

    def __init__(self, base_url: str, timeout_seconds: int, service_name: str) -> None:
        self._base_url = base_url
        self._service_name = service_name
        self._client = httpx.AsyncClient(
            base_url=base_url,
            timeout=httpx.Timeout(timeout_seconds),
        )

    async def _post(self, path: str, payload: dict, expected_status: int) -> dict:
        """Send POST request and handle common error patterns."""
        try:
            response = await self._client.post(path, json=payload)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ServiceError(
                error=f"{self._service_name}_unavailable",
                message=f"Cannot reach {self._service_name}: {exc}",
                status_code=502,
                details={"base_url": self._base_url, "path": path},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                error=f"{self._service_name}_unavailable",
                message=f"HTTP error from {self._service_name}: {exc}",
                status_code=502,
                details={"base_url": self._base_url, "path": path},
            )
        if response.status_code != expected_status:
            error_body = {}
            try:
                error_body = response.json()
            except Exception:
                pass
            raise ServiceError(
                error=f"{self._service_name}_error",
                message=f"Unexpected status {response.status_code} from {self._service_name}",
                status_code=response.status_code,
                details=error_body,
            )
        return response.json()

    async def _get(self, path: str) -> dict:
        """Send GET request and return JSON response."""
        try:
            response = await self._client.get(path)
        except (httpx.ConnectError, httpx.TimeoutException) as exc:
            raise ServiceError(
                error=f"{self._service_name}_unavailable",
                message=f"Cannot reach {self._service_name}: {exc}",
                status_code=502,
                details={"base_url": self._base_url, "path": path},
            )
        except httpx.HTTPError as exc:
            raise ServiceError(
                error=f"{self._service_name}_unavailable",
                message=f"HTTP error from {self._service_name}: {exc}",
                status_code=502,
                details={"base_url": self._base_url, "path": path},
            )
        if response.status_code == 404:
            return {}
        if response.status_code != 200:
            raise ServiceError(
                error=f"{self._service_name}_error",
                message=f"Unexpected status {response.status_code} from {self._service_name}",
                status_code=response.status_code,
                details={},
            )
        return response.json()

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._client.aclose()
```

#### Step 5: Create service-specific clients

For each client, read the EXISTING implementation in the service that uses it, then port the methods to the shared library. Use `self._post()` and `self._get()` from BaseServiceClient.

**`libs/service-clients/src/service_clients/identity.py`** — Port from `services/central-bank/src/central_bank_service/clients/identity_client.py`

**`libs/service-clients/src/service_clients/gateway.py`** — Port from `services/central-bank/src/central_bank_service/clients/gateway_client.py`

**`libs/service-clients/src/service_clients/bank.py`** — Port from `services/task-board/src/task_board_service/clients/central_bank_client.py`

For each:
1. Read the existing client file
2. Create the new client in the shared library extending BaseServiceClient
3. Port all methods, using `self._post()` / `self._get()` for common patterns
4. Add type annotations matching existing return types
5. Preserve service-specific error handling (e.g., 402 for insufficient funds)

#### Step 6: Do NOT wire services to use the new library yet

This ticket only creates the library. Wiring services to use it would be a separate ticket. Just create the library, make it importable, and verify it compiles.

### Verification

Since this is a new library without its own justfile/CI pipeline yet:
```bash
cd /Users/flo/Developer/github/agent-economy/libs/service-clients
uv sync
uv run python -c "from service_clients.base import BaseServiceClient; print('OK')"
uv run python -c "from service_clients.identity import IdentityClient; print('OK')"
uv run python -c "from service_clients.gateway import GatewayClient; print('OK')"
```

---

## TIER 3 VERIFICATION

```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet
```

**STOP HERE. Do not proceed to Tier 4 until `just ci-all-quiet` passes cleanly.**

---

# TIER 4: Gateway Constraint Support

---

## Ticket: agent-economy-2g1b — Add constraint support to DB Gateway write endpoints

### Overview

Extend all DB Gateway write endpoints to accept an optional `constraints` object. The gateway compiles constraints into WHERE clauses within atomic transactions, enabling optimistic concurrency.

### Design

Services submit constraints alongside writes:
```json
{
  "updates": {"status": "accepted", "worker_id": "a-bob"},
  "constraints": {"status": "open", "poster_id": "a-alice"},
  "event": {...}
}
```

Gateway compiles:
```sql
BEGIN IMMEDIATE;
UPDATE board_tasks
  SET status='accepted', worker_id='a-bob'
  WHERE task_id='t-123' AND status='open' AND poster_id='a-alice';
-- rowcount=0 → ROLLBACK, return constraint_violation error
-- rowcount=1 → INSERT INTO events; COMMIT
```

### Implementation Steps

#### Step 1: Add constraint validation helper

**File: `services/db-gateway/src/db_gateway_service/routers/helpers.py`**

Add a new function:

```python
def validate_constraints(data: dict) -> dict | None:
    """Validate and return constraints dict if present, or None."""
    constraints = data.get("constraints")
    if constraints is None:
        return None
    if not isinstance(constraints, dict):
        raise ServiceError(
            error="invalid_constraints",
            message="constraints must be a JSON object",
            status_code=400,
            details={},
        )
    if not constraints:
        return None  # Empty dict = no constraints
    return constraints
```

#### Step 2: Add constraint compilation in db_writer.py

**File: `services/db-gateway/src/db_gateway_service/services/db_writer.py`**

Add a private method to DbWriter:

```python
def _compile_constraints(self, table: str, pk_column: str, pk_value: str, constraints: dict) -> tuple[str, list]:
    """Compile constraints into a WHERE clause for UPDATE statements.

    Returns (where_clause, params) to append to an UPDATE statement.
    """
    where_parts = [f"{pk_column} = ?"]
    params = [pk_value]
    for col, expected in constraints.items():
        where_parts.append(f"{col} = ?")
        params.append(expected)
    return " AND ".join(where_parts), params

def _check_constraint_violation(self, cursor, table: str, pk_column: str, pk_value: str, constraints: dict) -> None:
    """After a 0-rowcount UPDATE, query actual values and raise descriptive error."""
    row = cursor.execute(
        f"SELECT * FROM {table} WHERE {pk_column} = ?", (pk_value,)
    ).fetchone()
    if row is None:
        raise ServiceError(
            error="not_found",
            message=f"No {table} row with {pk_column}={pk_value}",
            status_code=404,
            details={"table": table, pk_column: pk_value},
        )
    # Find which constraint failed
    row_dict = dict(row)
    for col, expected in constraints.items():
        actual = row_dict.get(col)
        if str(actual) != str(expected):
            raise ServiceError(
                error="constraint_violation",
                message=f"Expected {col}='{expected}' but found {col}='{actual}'",
                status_code=409,
                details={
                    "table": table,
                    "constraint": col,
                    "expected": str(expected),
                    "actual": str(actual),
                },
            )
```

#### Step 3: Add constraints to `update_task_status()`

In `update_task_status()`, modify the UPDATE query to include constraints:

```python
def update_task_status(self, task_id: str, data: dict, constraints: dict | None = None) -> dict:
    # ... existing validation ...

    if constraints:
        where_clause, where_params = self._compile_constraints(
            "board_tasks", "task_id", task_id, constraints
        )
        sql = f"UPDATE board_tasks SET {set_clause} WHERE {where_clause}"
        cursor.execute(sql, set_params + where_params)
    else:
        sql = f"UPDATE board_tasks SET {set_clause} WHERE task_id = ?"
        cursor.execute(sql, set_params + [task_id])

    if cursor.rowcount == 0:
        if constraints:
            self._check_constraint_violation(cursor, "board_tasks", "task_id", task_id, constraints)
        # ... existing 404 handling ...
```

**IMPORTANT:** Do NOT add default parameter values. The constraints parameter should be explicitly passed from the router, not defaulted. Actually, since some callers may not pass constraints, use `None` explicitly: `constraints: dict | None` and require callers to pass `None` explicitly.

Wait — the project rule says "Never use default parameter values for configurable settings." Constraints are not a configurable setting, they're a request parameter. Using `None` as default for an optional request parameter is acceptable. But to be safe, make the router always pass it explicitly.

#### Step 4: Add constraints to router endpoints

**File: `services/db-gateway/src/db_gateway_service/routers/board.py`**

In the `POST /board/tasks/{task_id}/status` endpoint, parse constraints:
```python
constraints = validate_constraints(data)
result = state.db_writer.update_task_status(task_id, data, constraints)
```

#### Step 5: Add constraints to other write endpoints as needed

Apply the same pattern to:
- `POST /bank/escrow/release` — constraint: `status = 'locked'`
- `POST /bank/escrow/split` — constraint: `status = 'locked'`
- `POST /court/rebuttals` — constraint on court_claims status
- `POST /board/bids` — cross-table constraint: verify task status = 'open'
- `POST /board/assets` — cross-table constraint: verify task status, worker_id

For cross-table constraints (SELECT check before INSERT), add a `_verify_cross_table_constraint()` method:

```python
def _verify_cross_table_constraint(self, cursor, table: str, conditions: dict) -> dict:
    """SELECT from another table to verify preconditions before INSERT."""
    where_parts = []
    params = []
    for col, expected in conditions.items():
        where_parts.append(f"{col} = ?")
        params.append(expected)
    where_clause = " AND ".join(where_parts)
    row = cursor.execute(f"SELECT * FROM {table} WHERE {where_clause}", params).fetchone()
    if row is None:
        raise ServiceError(
            error="constraint_violation",
            message=f"Cross-table constraint failed on {table}",
            status_code=409,
            details={"table": table, "conditions": conditions},
        )
    return dict(row)
```

#### Step 6: Write tests

**File: `services/db-gateway/tests/unit/test_constraints.py`**

```python
"""Tests for constraint support in DB Gateway write endpoints."""
import pytest

@pytest.mark.unit
class TestConstraintEnforcement:

    def test_update_task_with_valid_constraint(self, app_with_writer):
        """Update succeeds when constraint matches current state."""
        # Create agent, task, then update with matching constraint

    def test_update_task_with_violated_constraint(self, app_with_writer):
        """Update fails with 409 when constraint doesn't match."""
        # Create agent, task, update status to 'accepted'
        # Then try to update with constraint status='open' → 409

    def test_constraint_violation_error_format(self, app_with_writer):
        """Error response includes table, constraint, expected, actual."""
        # Verify error body has correct structure

    def test_update_without_constraints(self, app_with_writer):
        """Update works normally without constraints field."""
        # Existing behavior preserved

    def test_empty_constraints_object(self, app_with_writer):
        """Empty constraints dict is treated as no constraints."""

    def test_escrow_release_constraint(self, app_with_writer):
        """Escrow release with status='locked' constraint."""

    def test_cross_table_bid_constraint(self, app_with_writer):
        """Bid submission verifies task is still open."""
```

### Verification
```bash
cd /Users/flo/Developer/github/agent-economy/services/db-gateway && uv run just ci-quiet
```

---

## TIER 4 VERIFICATION

```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet
```

**STOP HERE. Do not proceed to Tier 5 until `just ci-all-quiet` passes cleanly.**

---

# TIER 5: Architecture Constraints

---

## Ticket: agent-economy-w9y0 — Architecture constraint: only StorageInterface implementations may call db-gateway (identity)

### Overview

Add an architectural test ensuring that only `GatewayAgentStore` may import `httpx` or reference the db-gateway URL in the identity service. No router, service layer, or other module should directly call the db-gateway.

### Implementation

#### Step 1: Read existing architecture tests

```
services/identity/tests/architecture/
services/db-gateway/tests/architecture/test_architecture.py
```

Understand the existing pattern for architecture tests (likely using `pytestarch` or import scanning).

#### Step 2: Create architecture test

**File: `services/identity/tests/architecture/test_gateway_isolation.py`**

```python
"""Architecture tests: only GatewayAgentStore may call db-gateway."""
import ast
import os

import pytest


@pytest.mark.architecture
class TestGatewayIsolation:

    def _get_imports(self, filepath: str) -> set[str]:
        """Extract all import module names from a Python file."""
        with open(filepath) as f:
            tree = ast.parse(f.read())
        imports = set()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.add(alias.name)
            elif isinstance(node, ast.ImportFrom):
                if node.module:
                    imports.add(node.module)
        return imports

    def _get_python_files(self, directory: str, exclude: list[str]) -> list[str]:
        """Get all .py files in directory, excluding specified paths."""
        files = []
        for root, _dirs, filenames in os.walk(directory):
            for fname in filenames:
                if not fname.endswith(".py"):
                    continue
                filepath = os.path.join(root, fname)
                if not any(exc in filepath for exc in exclude):
                    files.append(filepath)
        return files

    def test_only_gateway_store_imports_httpx(self):
        """No module except GatewayAgentStore should import httpx."""
        service_src = "src/identity_service"
        exclude = ["gateway_agent_store.py", "__pycache__"]

        for filepath in self._get_python_files(service_src, exclude):
            imports = self._get_imports(filepath)
            assert "httpx" not in imports, (
                f"{filepath} imports httpx but only GatewayAgentStore is allowed to"
            )

    def test_only_gateway_store_references_db_gateway(self):
        """No module except GatewayAgentStore should reference db-gateway URLs."""
        service_src = "src/identity_service"
        exclude = ["gateway_agent_store.py", "config.py", "__pycache__"]

        for filepath in self._get_python_files(service_src, exclude):
            with open(filepath) as f:
                content = f.read()
            assert "db_gateway" not in content.lower() or "config" in filepath, (
                f"{filepath} references db_gateway but only GatewayAgentStore is allowed to"
            )
```

### Verification
```bash
cd /Users/flo/Developer/github/agent-economy/services/identity && uv run just ci-quiet
```

---

## TIER 5 VERIFICATION

```bash
cd /Users/flo/Developer/github/agent-economy
just ci-all-quiet
```

---

# FINAL VERIFICATION

After ALL tiers are complete:

```bash
cd /Users/flo/Developer/github/agent-economy

# 1. Full CI
just ci-all-quiet

# 2. Start all services and check health
just start-all
sleep 5
just status

# 3. Stop services
just stop-all
```

All services must:
1. Pass CI (formatting, linting, type checking, security, tests)
2. Start successfully
3. Return healthy status

---

# SUMMARY: Execution Order

| Tier | Ticket | Session | Description |
|------|--------|---------|-------------|
| 1 | agent-economy-dbh1 | codex | Fix config imports in routers (4 services) |
| 1 | agent-economy-hhq9 | codex | Fix core imports in reputation services |
| 1 | agent-economy-5yv4 | codex | Fix observatory CI failures |
| 2 | agent-economy-zpby | codex | Fix schema divergences |
| 2 | agent-economy-t13s | codex | Write semgrep test fixtures |
| 3 | agent-economy-523w | codex | Define Store Protocols (4 services) |
| 3 | agent-economy-sun2 | codingagent | Create shared HTTP client library |
| 4 | agent-economy-2g1b | codex | Add constraint support to DB Gateway |
| 5 | agent-economy-w9y0 | codex | Architecture constraint test (identity) |
