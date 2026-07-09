# Database Abstraction Design

Extract SQLite persistence into dedicated store classes for Identity, Task Board, and Court services, following the pattern established by Reputation's `FeedbackStore`.

## Motivation

Three services (Identity, Task Board, Court) hardcode `sqlite3.connect()` inside their main business logic classes (`AgentRegistry`, `TaskManager`, `DisputeService`). This couples business logic to a specific storage implementation, making unit testing require real databases and preventing future backend migration.

Reputation already solved this with `FeedbackStore` — a concrete class that owns the SQLite connection and exposes named persistence methods. Business logic in `services/feedback.py` accepts `FeedbackStore` as a parameter and never imports `sqlite3`.

## Approach

Extract a concrete `*Store` class per service. No Protocols, no ABCs — just concrete classes following the Reputation pattern. Business logic classes receive the store via constructor injection.

## Identity Service

### New file: `services/identity/src/identity_service/services/agent_store.py`

```python
class AgentStore:
    def __init__(self, db_path: str) -> None: ...       # sqlite3.connect, PRAGMAs, schema init, RLock
    def insert(self, name: str, public_key: str) -> dict[str, str]: ...
    def get_by_id(self, agent_id: str) -> dict[str, str] | None: ...
    def list_all(self) -> list[dict[str, str]]: ...
    def count(self) -> int: ...
    def close(self) -> None: ...
```

Raises `DuplicateAgentError` on unique constraint violation (instead of raw `sqlite3.IntegrityError`).

### Changes to `AgentRegistry`

- Constructor takes `store: AgentStore` instead of `db_path: str`
- `register_agent()` calls `self._store.insert(...)` after validation
- `get_agent()` delegates to `self._store.get_by_id()`
- `list_agents()` delegates to `self._store.list_all()`
- `count_agents()` delegates to `self._store.count()`
- `close()` delegates to `self._store.close()`
- Crypto params (`algorithm`, `public_key_prefix`, etc.) stay in `AgentRegistry`

### Changes to `lifespan.py`

Create `AgentStore` first, then inject into `AgentRegistry`:

```python
store = AgentStore(db_path=settings.database.path)
state.registry = AgentRegistry(store=store, algorithm=..., ...)
```

### Changes to `state.py`

None. `AppState.registry` stays typed as `AgentRegistry`.

## Task Board Service

### New file: `services/task-board/src/task_board_service/services/task_store.py`

Largest extraction (~50 SQL calls). Handles three tables: `tasks`, `bids`, `assets`.

```python
class TaskStore:
    def __init__(self, db_path: str) -> None: ...

    # Tasks
    def insert_task(self, task_data: dict) -> dict: ...
    def get_task(self, task_id: str) -> dict | None: ...
    def update_task(self, task_id: str, updates: dict) -> None: ...
    def list_tasks(self, filters: dict) -> list[dict]: ...
    def count_tasks(self) -> int: ...
    def count_tasks_by_status(self) -> dict[str, int]: ...
    def get_stats(self) -> dict: ...

    # Bids
    def insert_bid(self, bid_data: dict) -> dict: ...
    def get_bid(self, bid_id: str) -> dict | None: ...
    def get_bids_for_task(self, task_id: str) -> list[dict]: ...
    def update_bid(self, bid_id: str, updates: dict) -> None: ...
    def update_bids_for_task(self, task_id: str, updates: dict) -> None: ...

    # Assets
    def insert_asset(self, asset_data: dict) -> dict: ...
    def get_asset(self, asset_id: str) -> dict | None: ...
    def get_assets_for_task(self, task_id: str) -> list[dict]: ...
    def delete_assets_for_task(self, task_id: str) -> None: ...

    def close(self) -> None: ...
```

### Changes to `TaskManager`

- Constructor takes `store: TaskStore` instead of `db_path: str`
- All `self._db.execute(...)` calls replaced with `self._store.*()` calls
- Business logic stays: validation, deadline evaluation, escrow coordination, JWS verification, file I/O
- Constructor still takes `identity_client`, `central_bank_client`, `platform_signer` (separate concern)

### Changes to `lifespan.py`

Create `TaskStore` first, pass to `TaskManager`.

### Changes to `state.py`

None. `AppState.task_manager` stays typed as `TaskManager`. The `__setattr__` magic (wiring HTTP clients) is unrelated.

## Court Service

### New file: `services/court/src/court_service/services/dispute_store.py`

Two tables: `disputes`, `votes`.

```python
class DisputeStore:
    def __init__(self, db_path: str) -> None: ...

    # Disputes
    def insert_dispute(self, dispute_data: dict) -> dict: ...
    def get_dispute(self, dispute_id: str) -> dict | None: ...
    def update_dispute(self, dispute_id: str, updates: dict) -> None: ...
    def list_disputes(self, task_id: str | None, status: str | None) -> list[dict]: ...
    def count_disputes(self) -> int: ...
    def count_active(self) -> int: ...

    # Votes
    def insert_votes(self, dispute_id: str, votes: list[dict]) -> None: ...
    def get_votes(self, dispute_id: str) -> list[dict]: ...

    # Atomic operations
    def persist_ruling(self, dispute_id: str, ruling_data: dict, votes: list[dict]) -> None: ...
        # BEGIN IMMEDIATE, UPDATE dispute + INSERT votes, COMMIT
        # On error: ROLLBACK + revert status

    def close(self) -> None: ...
```

Raises `DuplicateDisputeError` on unique constraint violation.

### Changes to `DisputeService`

- Constructor takes `store: DisputeStore` instead of `db_path: str`
- `file_dispute()` calls `self._store.insert_dispute()` after validation
- `submit_rebuttal()` calls `self._store.update_dispute()`
- `_validate_ruling_preconditions()` calls `self._store.get_dispute()`
- `_persist_ruling()` delegates to `self._store.persist_ruling()`
- Orchestration stays: judge evaluation, escrow splitting, feedback recording

### Changes to `lifespan.py`

Create `DisputeStore` first, inject into `DisputeService`.

### Changes to `state.py`

None. `AppState.dispute_service` stays typed as `DisputeService`.

## Consistency Standardization

Align all new store classes with Reputation's `FeedbackStore` patterns:

| Pattern | Identity | Task Board | Court |
|---------|:-------:|:---------:|:-----:|
| RLock on all SQL ops | Add | Add | Already has |
| `row_factory = sqlite3.Row` | Add | Add | Already has |
| PRAGMAs: WAL + FK + timeout | Add FK, timeout | Add FK, timeout | Already has |
| Custom duplicate exception | `DuplicateAgentError` | N/A | `DuplicateDisputeError` |
| `Path.parent.mkdir()` in store | Move from lifespan | Move from lifespan | Already there |

## Out of Scope

- No Protocols or ABCs
- No changes to HTTP clients
- No decomposition of TaskManager beyond store extraction (separate issue)
- No decomposition of DisputeService beyond store extraction (separate issue)
- No crypto/JWS split in Identity
- Existing tests must not be modified
