# Database Abstraction Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Extract SQLite persistence from business logic into dedicated store classes for Identity, Task Board, and Court services.

**Architecture:** Each service gets a new `*_store.py` file that owns the SQLite connection, schema, and all SQL operations. Business logic classes receive the store via constructor injection and never import `sqlite3`. Follows the pattern from Reputation's `FeedbackStore`.

**Tech Stack:** Python, SQLite, FastAPI, pytest

**Key references:**
- Design doc: `docs/plans/2026-03-01-database-abstraction-design.md`
- Reference implementation: `services/reputation/src/reputation_service/services/feedback_store.py`
- Reference tests: `services/reputation/tests/unit/test_store_robustness.py`
- Project conventions: `CLAUDE.md`

**Beads issue:** `agent-economy-xkb`

---

## Task 1: Identity — Create `AgentStore`

**Files:**
- Create: `services/identity/src/identity_service/services/agent_store.py`
- Test: `services/identity/tests/unit/test_agent_store.py`

**Step 1: Write the failing tests**

Create `services/identity/tests/unit/test_agent_store.py`:

```python
"""Unit tests for AgentStore SQLite persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
class TestAgentStore:
    """Tests for AgentStore CRUD operations."""

    def _make_store(self, tmp_path: Path):
        from identity_service.services.agent_store import AgentStore
        return AgentStore(db_path=str(tmp_path / "test.db"))

    def test_insert_and_get_by_id(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        result = store.insert("alice", "ed25519:AAAA")
        assert result["name"] == "alice"
        assert result["public_key"] == "ed25519:AAAA"
        assert "agent_id" in result
        assert "registered_at" in result

        fetched = store.get_by_id(result["agent_id"])
        assert fetched is not None
        assert fetched["agent_id"] == result["agent_id"]
        store.close()

    def test_get_by_id_not_found(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        assert store.get_by_id("nonexistent") is None
        store.close()

    def test_list_all(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert("alice", "ed25519:AAAA")
        store.insert("bob", "ed25519:BBBB")
        agents = store.list_all()
        assert len(agents) == 2
        assert agents[0]["name"] == "alice"
        assert agents[1]["name"] == "bob"
        # list_all omits public_key
        assert "public_key" not in agents[0]
        store.close()

    def test_count(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        assert store.count() == 0
        store.insert("alice", "ed25519:AAAA")
        assert store.count() == 1
        store.close()

    def test_duplicate_public_key_raises(self, tmp_path: Path) -> None:
        from identity_service.services.agent_store import DuplicateAgentError
        store = self._make_store(tmp_path)
        store.insert("alice", "ed25519:AAAA")
        with pytest.raises(DuplicateAgentError):
            store.insert("bob", "ed25519:AAAA")
        store.close()

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        nested = tmp_path / "nested" / "dir" / "test.db"
        from identity_service.services.agent_store import AgentStore
        store = AgentStore(db_path=str(nested))
        assert nested.parent.exists()
        store.close()
```

**Step 2: Run tests to verify they fail**

Run from `services/identity/`:
```bash
uv run pytest tests/unit/test_agent_store.py -v
```
Expected: FAIL with `ModuleNotFoundError` (agent_store does not exist yet)

**Step 3: Implement `AgentStore`**

Create `services/identity/src/identity_service/services/agent_store.py`. Move all SQLite code from `AgentRegistry`:
- `__init__`: `sqlite3.connect`, `PRAGMA journal_mode=WAL`, `PRAGMA foreign_keys=ON`, `PRAGMA busy_timeout=5000`, `RLock`, `Path.parent.mkdir`, `_init_schema()`
- `_init_schema()`: `CREATE TABLE IF NOT EXISTS agents`
- `insert()`: `INSERT INTO agents` with `uuid.uuid4()`, `datetime.now(UTC)`, catches `sqlite3.IntegrityError` → raises `DuplicateAgentError`
- `get_by_id()`: `SELECT ... WHERE agent_id = ?`
- `list_all()`: `SELECT agent_id, name, registered_at FROM agents ORDER BY registered_at` (omits public_key)
- `count()`: `SELECT COUNT(*) FROM agents`
- `close()`: `self._db.close()`

Define `DuplicateAgentError(Exception)` in the same file.

**Step 4: Run tests to verify they pass**

```bash
uv run pytest tests/unit/test_agent_store.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add services/identity/src/identity_service/services/agent_store.py services/identity/tests/unit/test_agent_store.py
git commit -m "feat(identity): add AgentStore for SQLite persistence extraction"
```

---

## Task 2: Identity — Refactor `AgentRegistry` to use `AgentStore`

**Files:**
- Modify: `services/identity/src/identity_service/services/agent_registry.py`
- Modify: `services/identity/src/identity_service/services/__init__.py`
- Modify: `services/identity/src/identity_service/core/lifespan.py`

**Step 1: Refactor `AgentRegistry`**

Changes to `agent_registry.py`:
- Remove `import sqlite3`, `import uuid`, `from datetime import UTC, datetime`
- Remove `from pathlib import Path` if only used for db
- Constructor: replace `db_path: str` with `store: AgentStore`. Remove `self._db = sqlite3.connect(...)`, `PRAGMA`, `_init_schema()`. Store as `self._store = store`.
- `register_agent()`: replace `self._db.execute("INSERT ...")` with `self._store.insert(name, public_key)`. Catch `DuplicateAgentError` and re-raise as `ServiceError("PUBLIC_KEY_EXISTS", ...)`.
- `get_agent()`: replace SQL with `self._store.get_by_id(agent_id)`
- `list_agents()`: replace SQL with `self._store.list_all()`
- `count_agents()`: replace SQL with `self._store.count()`
- `close()`: replace `self._db.close()` with `self._store.close()`
- Remove `_init_schema()` method entirely

Update `services/__init__.py` to also export `AgentStore`:
```python
from identity_service.services.agent_store import AgentStore
```

**Step 2: Refactor `lifespan.py`**

Change initialization order:
```python
from identity_service.services.agent_store import AgentStore

# In lifespan():
store = AgentStore(db_path=db_path)
state.registry = AgentRegistry(
    store=store,
    algorithm=settings.crypto.algorithm,
    public_key_prefix=settings.crypto.public_key_prefix,
    public_key_bytes=settings.crypto.public_key_bytes,
    signature_bytes=settings.crypto.signature_bytes,
)
```

Remove the `Path(db_path).parent.mkdir(...)` from lifespan (now in `AgentStore.__init__`).

**Step 3: Run all Identity tests**

```bash
cd services/identity && uv run pytest tests/ -v
```
Expected: all existing tests PASS (behavior unchanged)

**Step 4: Run CI checks**

```bash
cd services/identity && just ci-quiet
```
Expected: PASS

**Step 5: Commit**

```bash
git add services/identity/
git commit -m "refactor(identity): inject AgentStore into AgentRegistry"
```

---

## Task 3: Court — Create `DisputeStore`

**Files:**
- Create: `services/court/src/court_service/services/dispute_store.py`
- Test: `services/court/tests/unit/test_dispute_store.py`

**Step 1: Write the failing tests**

Create `services/court/tests/unit/test_dispute_store.py`:

```python
"""Unit tests for DisputeStore SQLite persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
class TestDisputeStore:
    """Tests for DisputeStore CRUD operations."""

    def _make_store(self, tmp_path: Path):
        from court_service.services.dispute_store import DisputeStore
        return DisputeStore(db_path=str(tmp_path / "test.db"))

    def _sample_dispute(self, **overrides) -> dict:
        base = {
            "task_id": "task-1",
            "claimant_id": "agent-alice",
            "respondent_id": "agent-bob",
            "claim": "Work not delivered",
            "escrow_id": "esc-1",
        }
        base.update(overrides)
        return base

    def test_insert_and_get(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        result = store.insert_dispute(self._sample_dispute())
        assert result["task_id"] == "task-1"
        assert result["claimant_id"] == "agent-alice"
        assert "dispute_id" in result
        assert result["status"] == "filed"

        fetched = store.get_dispute(result["dispute_id"])
        assert fetched is not None
        assert fetched["dispute_id"] == result["dispute_id"]
        store.close()

    def test_get_not_found(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        assert store.get_dispute("nonexistent") is None
        store.close()

    def test_update_dispute(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        result = store.insert_dispute(self._sample_dispute())
        store.update_dispute(result["dispute_id"], {"status": "rebuttal_pending", "rebuttal": "I did deliver"})
        fetched = store.get_dispute(result["dispute_id"])
        assert fetched is not None
        assert fetched["status"] == "rebuttal_pending"
        store.close()

    def test_list_disputes(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert_dispute(self._sample_dispute(task_id="task-1"))
        store.insert_dispute(self._sample_dispute(task_id="task-2"))
        all_disputes = store.list_disputes(task_id=None, status=None)
        assert len(all_disputes) == 2
        store.close()

    def test_list_disputes_filter_by_task(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert_dispute(self._sample_dispute(task_id="task-1"))
        store.insert_dispute(self._sample_dispute(task_id="task-2"))
        filtered = store.list_disputes(task_id="task-1", status=None)
        assert len(filtered) == 1
        assert filtered[0]["task_id"] == "task-1"
        store.close()

    def test_count(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        assert store.count_disputes() == 0
        store.insert_dispute(self._sample_dispute())
        assert store.count_disputes() == 1
        store.close()

    def test_insert_votes_and_get(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        dispute = store.insert_dispute(self._sample_dispute())
        votes = [
            {"judge_name": "judge-1", "claimant_pct": 70, "respondent_pct": 30, "reasoning": "Mostly claimant"},
        ]
        store.insert_votes(dispute["dispute_id"], votes)
        fetched_votes = store.get_votes(dispute["dispute_id"])
        assert len(fetched_votes) == 1
        assert fetched_votes[0]["judge_name"] == "judge-1"
        store.close()

    def test_duplicate_dispute_raises(self, tmp_path: Path) -> None:
        from court_service.services.dispute_store import DuplicateDisputeError
        store = self._make_store(tmp_path)
        store.insert_dispute(self._sample_dispute())
        with pytest.raises(DuplicateDisputeError):
            store.insert_dispute(self._sample_dispute())
        store.close()

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        from court_service.services.dispute_store import DisputeStore
        nested = tmp_path / "nested" / "dir" / "test.db"
        store = DisputeStore(db_path=str(nested))
        assert nested.parent.exists()
        store.close()
```

**Step 2: Run tests to verify they fail**

```bash
cd services/court && uv run pytest tests/unit/test_dispute_store.py -v
```
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement `DisputeStore`**

Create `services/court/src/court_service/services/dispute_store.py`. Move all SQLite code from `DisputeService`:
- `__init__`: `sqlite3.connect`, PRAGMAs (WAL, FK, timeout), `row_factory`, `RLock`, `Path.parent.mkdir`, `_init_schema()`
- `_init_schema()`: `CREATE TABLE disputes`, `CREATE TABLE votes`, indexes
- `insert_dispute()`: `BEGIN IMMEDIATE`, `INSERT INTO disputes`, catches `IntegrityError` → `DuplicateDisputeError`
- `get_dispute()`: `SELECT * FROM disputes WHERE dispute_id = ?`
- `update_dispute()`: dynamically builds `UPDATE disputes SET ... WHERE dispute_id = ?`
- `list_disputes()`: `SELECT` with optional `task_id`/`status` filters
- `count_disputes()`, `count_active()`: `COUNT(*)` queries
- `insert_votes()`: batch `INSERT INTO votes`
- `get_votes()`: `SELECT * FROM votes WHERE dispute_id = ?`
- `persist_ruling()`: atomic `BEGIN IMMEDIATE` → `UPDATE disputes` + `INSERT votes` → `COMMIT`, reverts on error
- `close()`: `self._db.close()`

Define `DuplicateDisputeError(Exception)`.

**Step 4: Run tests to verify they pass**

```bash
cd services/court && uv run pytest tests/unit/test_dispute_store.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add services/court/src/court_service/services/dispute_store.py services/court/tests/unit/test_dispute_store.py
git commit -m "feat(court): add DisputeStore for SQLite persistence extraction"
```

---

## Task 4: Court — Refactor `DisputeService` to use `DisputeStore`

**Files:**
- Modify: `services/court/src/court_service/services/dispute_service.py`
- Modify: `services/court/src/court_service/services/__init__.py`
- Modify: `services/court/src/court_service/core/lifespan.py`

**Step 1: Refactor `DisputeService`**

Changes to `dispute_service.py`:
- Remove `import sqlite3` and `from threading import RLock` and `from pathlib import Path`
- Constructor: replace `db_path: str` with `store: DisputeStore`. Remove `self._db`, `self._lock`, PRAGMAs, `_init_schema()`. Store as `self._store = store`.
- `file_dispute()`: replace SQL with `self._store.insert_dispute(...)`. Catch `DuplicateDisputeError` → `ServiceError`.
- `submit_rebuttal()`: replace SQL with `self._store.get_dispute()` + `self._store.update_dispute()`
- `_validate_ruling_preconditions()`: replace SQL with `self._store.get_dispute()`
- `_persist_ruling()`: delegate to `self._store.persist_ruling()`
- `get_dispute()`: replace SQL with `self._store.get_dispute()` + `self._store.get_votes()`
- `list_disputes()`: replace SQL with `self._store.list_disputes()`
- `count_disputes()`, `count_active()`: delegate to store
- `close()`: delegate to `self._store.close()`
- Remove `_init_schema()`, `_row_to_dispute()` (move row conversion to store), `_get_dispute_row()` methods

Update `services/__init__.py` to also export `DisputeStore`.

**Step 2: Refactor `lifespan.py`**

```python
from court_service.services.dispute_store import DisputeStore

# In lifespan():
store = DisputeStore(db_path=db_path)
state.dispute_service = DisputeService(store=store)
```

Remove `Path(db_path).parent.mkdir(...)` from lifespan.

**Step 3: Run all Court tests**

```bash
cd services/court && uv run pytest tests/ -v
```
Expected: all existing tests PASS

**Step 4: Run CI checks**

```bash
cd services/court && just ci-quiet
```
Expected: PASS

**Step 5: Commit**

```bash
git add services/court/
git commit -m "refactor(court): inject DisputeStore into DisputeService"
```

---

## Task 5: Task Board — Create `TaskStore`

**Files:**
- Create: `services/task-board/src/task_board_service/services/task_store.py`
- Test: `services/task-board/tests/unit/test_task_store.py`

This is the largest store. Read `services/task-board/src/task_board_service/services/task_manager.py` fully before starting. Identify every `self._db.execute(...)` call and group them by entity (tasks, bids, assets).

**Step 1: Write the failing tests**

Create `services/task-board/tests/unit/test_task_store.py`:

```python
"""Unit tests for TaskStore SQLite persistence."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

if TYPE_CHECKING:
    from pathlib import Path


@pytest.mark.unit
class TestTaskStore:
    """Tests for TaskStore CRUD operations."""

    def _make_store(self, tmp_path: Path):
        from task_board_service.services.task_store import TaskStore
        return TaskStore(db_path=str(tmp_path / "test.db"))

    def _sample_task(self, **overrides) -> dict:
        base = {
            "task_id": "task-1",
            "poster_id": "agent-alice",
            "title": "Test task",
            "spec": "Do the thing",
            "reward": 100,
            "escrow_id": "esc-1",
            "status": "open",
        }
        base.update(overrides)
        return base

    def test_insert_and_get_task(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert_task(self._sample_task())
        fetched = store.get_task("task-1")
        assert fetched is not None
        assert fetched["poster_id"] == "agent-alice"
        store.close()

    def test_get_task_not_found(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        assert store.get_task("nonexistent") is None
        store.close()

    def test_update_task(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert_task(self._sample_task())
        store.update_task("task-1", {"status": "assigned"})
        fetched = store.get_task("task-1")
        assert fetched is not None
        assert fetched["status"] == "assigned"
        store.close()

    def test_count_tasks(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        assert store.count_tasks() == 0
        store.insert_task(self._sample_task())
        assert store.count_tasks() == 1
        store.close()

    def test_insert_and_get_bid(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert_task(self._sample_task())
        bid = {"bid_id": "bid-1", "task_id": "task-1", "bidder_id": "agent-bob", "amount": 80, "status": "pending"}
        store.insert_bid(bid)
        fetched = store.get_bid("bid-1")
        assert fetched is not None
        assert fetched["bidder_id"] == "agent-bob"
        store.close()

    def test_get_bids_for_task(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert_task(self._sample_task())
        store.insert_bid({"bid_id": "bid-1", "task_id": "task-1", "bidder_id": "agent-bob", "amount": 80, "status": "pending"})
        store.insert_bid({"bid_id": "bid-2", "task_id": "task-1", "bidder_id": "agent-charlie", "amount": 90, "status": "pending"})
        bids = store.get_bids_for_task("task-1")
        assert len(bids) == 2
        store.close()

    def test_insert_and_get_asset(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert_task(self._sample_task())
        asset = {"asset_id": "asset-1", "task_id": "task-1", "filename": "report.pdf", "uploader_id": "agent-bob"}
        store.insert_asset(asset)
        fetched = store.get_asset("asset-1")
        assert fetched is not None
        assert fetched["filename"] == "report.pdf"
        store.close()

    def test_delete_assets_for_task(self, tmp_path: Path) -> None:
        store = self._make_store(tmp_path)
        store.insert_task(self._sample_task())
        store.insert_asset({"asset_id": "asset-1", "task_id": "task-1", "filename": "a.pdf", "uploader_id": "agent-bob"})
        store.delete_assets_for_task("task-1")
        assert store.get_assets_for_task("task-1") == []
        store.close()

    def test_creates_parent_directory(self, tmp_path: Path) -> None:
        from task_board_service.services.task_store import TaskStore
        nested = tmp_path / "nested" / "dir" / "test.db"
        store = TaskStore(db_path=str(nested))
        assert nested.parent.exists()
        store.close()
```

**Step 2: Run tests to verify they fail**

```bash
cd services/task-board && uv run pytest tests/unit/test_task_store.py -v
```
Expected: FAIL with `ModuleNotFoundError`

**Step 3: Implement `TaskStore`**

Create `services/task-board/src/task_board_service/services/task_store.py`. This is the largest file. Read `task_manager.py` fully. Move all `self._db` operations:
- `__init__`: `sqlite3.connect`, PRAGMAs (WAL, FK, timeout), `row_factory`, `RLock`, `Path.parent.mkdir`, `_init_schema()`
- `_init_schema()`: all `CREATE TABLE` statements (tasks, bids, assets)
- Task methods: `insert_task`, `get_task`, `update_task`, `list_tasks`, `count_tasks`, `count_tasks_by_status`, `get_stats`
- Bid methods: `insert_bid`, `get_bid`, `get_bids_for_task`, `update_bid`, `update_bids_for_task`
- Asset methods: `insert_asset`, `get_asset`, `get_assets_for_task`, `delete_assets_for_task`
- `close()`

The exact column names and SQL must match what `TaskManager` currently uses. Read the schema from `_init_schema()` carefully.

**Step 4: Run tests to verify they pass**

```bash
cd services/task-board && uv run pytest tests/unit/test_task_store.py -v
```
Expected: all PASS

**Step 5: Commit**

```bash
git add services/task-board/src/task_board_service/services/task_store.py services/task-board/tests/unit/test_task_store.py
git commit -m "feat(task-board): add TaskStore for SQLite persistence extraction"
```

---

## Task 6: Task Board — Refactor `TaskManager` to use `TaskStore`

**Files:**
- Modify: `services/task-board/src/task_board_service/services/task_manager.py`
- Modify: `services/task-board/src/task_board_service/services/__init__.py`
- Modify: `services/task-board/src/task_board_service/core/lifespan.py`

This is the largest refactor (2108 lines, ~50 SQL calls to replace). Work method by method.

**Step 1: Refactor `TaskManager`**

Changes to `task_manager.py`:
- Remove `import sqlite3`
- Constructor: replace `db_path: str` with `store: TaskStore`. Remove `self._db`, PRAGMAs, `_init_schema()`. Store as `self._store = store`.
- Replace every `self._db.execute(...)` with the corresponding `self._store.*()` call
- Remove `_init_schema()` method
- `close()`: delegate to `self._store.close()`

Update `services/__init__.py` to export `TaskStore`.

**Step 2: Refactor `lifespan.py`**

```python
from task_board_service.services.task_store import TaskStore

# In lifespan():
store = TaskStore(db_path=db_path)
task_manager = TaskManager(
    store=store,
    identity_client=identity_client,
    central_bank_client=central_bank_client,
    platform_signer=platform_signer,
    platform_agent_id=settings.platform.agent_id,
    asset_storage_path=asset_storage_path,
    max_file_size=max_file_size,
    max_files_per_task=max_files_per_task,
)
```

Remove `db_directory.mkdir(...)` from lifespan (now in `TaskStore.__init__`).

**Step 3: Run all Task Board tests**

```bash
cd services/task-board && uv run pytest tests/ -v
```
Expected: all existing tests PASS

**Step 4: Run CI checks**

```bash
cd services/task-board && just ci-quiet
```
Expected: PASS

**Step 5: Commit**

```bash
git add services/task-board/
git commit -m "refactor(task-board): inject TaskStore into TaskManager"
```

---

## Task 7: Final verification

**Step 1: Run all tests across affected services**

```bash
just test-all
```
Expected: all PASS

**Step 2: Run full CI**

```bash
just ci-all-quiet
```
Expected: PASS

**Step 3: Close beads issue**

```bash
bd close agent-economy-xkb --reason="Extracted AgentStore, TaskStore, DisputeStore from business logic classes"
```
