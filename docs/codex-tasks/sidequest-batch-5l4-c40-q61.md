# Sidequest Batch: agent-economy-5l4, c40, q61

Three independent tasks. Execute in order. Each has its own verification step.

---

## Task 1: Feeder Auto-Approval Review Loop (agent-economy-5l4)

**Goal:** Create `agents/src/task_feeder/review.py` that implements the `ReviewLoop` class and `check_answer` function. Wire it into `__main__.py`. Add config fields. Existing tests in `agents/tests/unit/task_feeder/test_review.py` must pass.

### Files to Read First

1. `agents/tests/unit/task_feeder/test_review.py` — the acceptance tests (source of truth for interface)
2. `agents/src/task_feeder/loop.py` — the existing feed loop
3. `agents/src/task_feeder/config.py` — config model
4. `agents/src/task_feeder/__main__.py` — entry point
5. `agents/src/task_feeder/reader.py` — RawTask dataclass
6. `agents/src/base_agent/mixins/task_board.py` — approve_task / dispute_task / list_tasks / get_task methods
7. `agents/config.yaml` — add new config fields here

### Step 1.1: Create `agents/src/task_feeder/review.py`

Create the file with two public symbols: `check_answer` and `ReviewLoop`.

**`check_answer(submitted: str, solutions: list[str]) -> bool`**
- Strip whitespace and lowercase both `submitted` and each solution
- Return `True` if stripped+lowered `submitted` matches any stripped+lowered solution
- Return `False` if `submitted` is empty (after stripping) or `solutions` is empty

**`class ReviewLoop`**
- Constructor: `__init__(self, agent: BaseAgent, task_map: dict[str, RawTask]) -> None`
  - Store agent as `self._agent`
  - Store task_map as `self._task_map`
- Method: `async def review_one(self, task_id: str) -> str`
  - If `task_id` not in `self._task_map`: raise `LookupError(f"Unknown task: {task_id}")`
  - Call `await self._agent.get_task(task_id)` to get the task payload dict
  - Extract submitted answer: try `payload["submitted_answer"]`, fallback `payload["submission"]["answer"]`, fallback `payload["deliverable"]["answer"]`
  - Look up `raw_task = self._task_map[task_id]`
  - Call `check_answer(submitted_answer, raw_task.solutions)`
  - If correct: `await self._agent.approve_task(task_id)`, return `"approved"`
  - If wrong: build reason string containing "Expected" and "got" and the submitted value, call `await self._agent.dispute_task(task_id, reason)`, return `"disputed"`
- Method: `async def run(self, interval_seconds: int) -> None`
  - Loop: poll `self._agent.list_tasks(status="SUBMITTED", poster_id=self._agent.agent_id)`
  - For each task with status "submitted" (case-insensitive) whose task_id is in self._task_map, call `review_one(task_id)`
  - Catch and log exceptions per-task (do not crash the loop)
  - Sleep `interval_seconds` between polls
  - Exit when `self._running` is False
- Property: `_running: bool` (default True), method `stop()` to set it False

Use these imports:
```python
from __future__ import annotations
import asyncio
import logging
from typing import TYPE_CHECKING
if TYPE_CHECKING:
    from base_agent.agent import BaseAgent
    from task_feeder.reader import RawTask
```

### Step 1.2: Update `agents/src/task_feeder/config.py`

Add two fields to `TaskFeederConfig`:
```python
review_interval_seconds: int
auto_approve_on_error: bool
```

### Step 1.3: Update `agents/config.yaml`

Add to the `task_feeder:` section:
```yaml
  review_interval_seconds: 30
  auto_approve_on_error: false
```

### Step 1.4: Update `agents/src/task_feeder/loop.py`

The feed loop must build and expose the `task_id -> RawTask` mapping so the review loop can use it.

- Add a `self._task_map: dict[str, RawTask] = {}` to `__init__`
- In `_feed_one`, after a successful `post_task()` call, store: `self._task_map[task_id] = raw_task` (where `task_id = result.get("task_id", "unknown")`)
- Add a read-only property: `@property def task_map(self) -> dict[str, RawTask]: return self._task_map`

### Step 1.5: Update `agents/src/task_feeder/__main__.py`

Wire the review loop to run concurrently with the feed loop:

1. Import `ReviewLoop` from `task_feeder.review`
2. After creating `loop = TaskFeederLoop(...)`, create the review loop:
   ```python
   review = ReviewLoop(agent=agent, task_map=loop.task_map)
   ```
3. Start both as concurrent tasks:
   ```python
   feed_task = asyncio.create_task(loop.run())
   review_task = asyncio.create_task(review.run(feeder_config.review_interval_seconds))
   ```
4. Update the signal handler to stop both:
   ```python
   def _handle_signal() -> None:
       logger.info("Received shutdown signal")
       loop.stop()
       review.stop()
   ```
5. Await both tasks:
   ```python
   try:
       await asyncio.gather(feed_task, review_task)
   finally:
       await agent.close()
   ```

### Verification for Task 1

```bash
cd /Users/flo/Developer/github/agent-economy/agents && uv run pytest tests/unit/task_feeder/test_review.py -v
```

All tests in `test_review.py` must pass. Then run:

```bash
cd /Users/flo/Developer/github/agent-economy/agents && just ci-quiet
```

---

## Task 2: Events Architecture Investigation (agent-economy-c40)

**Goal:** Produce a concise markdown document at `docs/plans/events-architecture.md` (under 200 lines).

### Files to Read First

1. `docs/specifications/schema.sql` — events table schema and payload reference (look for `CREATE TABLE events` and the comment block below it listing all event types)
2. `tools/seed-economy.sh` — how the `emit()` function writes events (this is the de facto spec for event emission)
3. `services/observatory/src/observatory_service/services/events.py` — how events are consumed (read-only)
4. `services/task-board/src/task_board_service/services/task_manager.py` — task lifecycle mutations (the biggest event emitter)
5. `services/central-bank/src/central_bank_service/services/ledger.py` — bank mutations
6. `services/identity/src/identity_service/services/agent_registry.py` — agent registration
7. `services/reputation/src/reputation_service/services/feedback.py` — feedback submission

### Step 2.1: Write the Document

Create `docs/plans/events-architecture.md` with these sections:

1. **Overview** — The events table is a shared append-only log. Observatory is the only consumer. No service currently writes events in production; only `tools/seed-economy.sh` populates them.

2. **Event Types by Service** — Table mapping each `event_source` to its `event_type` values (from schema.sql comments):

   | Source | Event Types |
   |---|---|
   | identity | agent.registered |
   | bank | account.created, salary.paid, escrow.locked, escrow.released, escrow.split |
   | board | task.created, task.cancelled, task.expired, bid.submitted, task.accepted, asset.uploaded, task.submitted, task.approved, task.auto_approved, task.disputed, task.ruled |
   | reputation | feedback.revealed |
   | court | claim.filed, rebuttal.submitted, ruling.delivered |

3. **Emission Points** — For each service, list the exact file and method where each event should be emitted (after the mutation succeeds):

   - **Identity**: `services/agent_registry.py::register_agent()` → `identity/agent.registered`
   - **Central Bank**: `services/ledger.py` → `create_account()`, `credit()` (salary only), `escrow_lock()`, `escrow_release()`, `escrow_split()`
   - **Task Board**: `services/task_manager.py` → all lifecycle transitions; `services/asset_manager.py` → `upload_asset()`; `services/deadline_evaluator.py` → auto-approve, expire transitions
   - **Reputation**: `services/feedback.py::submit_feedback()` → only when `visible=True` (mutual reveal)
   - **Court**: `services/dispute_service.py` → `file_dispute()`, `submit_rebuttal()`; `services/ruling_orchestrator.py` → ruling delivery

4. **Architecture Decision: Direct SQLite vs Central Collector**

   Option A — Direct SQLite writes: Each service opens the shared `economy.db` and INSERTs into `events`. Simple, no network hop. Risk: multiple writers to same SQLite file (WAL mode handles this for moderate concurrency).

   Option B — Central event collector: A new HTTP endpoint (or extend Observatory) accepts event POST requests and writes them. More complexity, but cleaner separation.

   **Recommendation**: Option A (direct SQLite) for now. The economy is a single-host simulation. Each service already shares the same `economy.db` path via config. SQLite WAL mode handles concurrent writers. The seed script already demonstrates this pattern. Migrate to Option B only when moving to multi-host deployment.

5. **Implementation Sketch** — A shared `EventWriter` class in `libs/service-commons/` that takes a DB path and provides `async def emit(source, event_type, task_id, agent_id, summary, payload)`.

### Verification for Task 2

The file `docs/plans/events-architecture.md` must exist and be under 200 lines. No code changes, no tests to run. Just verify the file reads correctly:

```bash
wc -l /Users/flo/Developer/github/agent-economy/docs/plans/events-architecture.md
```

---

## Task 3: Observatory DB Helper Consolidation (agent-economy-q61)

**Goal:** Add `execute_scalar`, `execute_fetchone`, `execute_fetchall` to `services/observatory/src/observatory_service/services/database.py`. Replace all duplicated private helpers across `metrics.py`, `agents.py`, `tasks.py`, `quarterly.py`. Also extract a `to_iso()` utility and `utc_now()` into `database.py`. All existing tests must continue to pass.

### Files to Read First

1. `services/observatory/src/observatory_service/services/database.py` — current helpers
2. `services/observatory/src/observatory_service/services/metrics.py` — duplicated `_scalar`, `now_iso`, `_now`
3. `services/observatory/src/observatory_service/services/agents.py` — duplicated `_scalar`, `_fetchone`, `_fetchall`
4. `services/observatory/src/observatory_service/services/tasks.py` — duplicated `_scalar`, `_fetchone`, `_fetchall`
5. `services/observatory/src/observatory_service/services/quarterly.py` — duplicated `_scalar`, `_fetchone`, `_fetchall`, `_now`
6. `services/observatory/tests/unit/test_database.py` — existing tests for database.py

### Step 3.1: Extend `database.py` with New Helpers

Add these functions to `services/observatory/src/observatory_service/services/database.py`:

```python
async def execute_scalar(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> Any:
    """Execute query and return the first column of the first row."""
    async with db.execute(sql, params) as cursor:
        row = await cursor.fetchone()
    if row is None:
        return None
    return row[0]


async def execute_fetchone(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> Any:
    """Execute query and return the first row."""
    async with db.execute(sql, params) as cursor:
        return await cursor.fetchone()


async def execute_fetchall(
    db: aiosqlite.Connection,
    sql: str,
    params: tuple[Any, ...],
) -> list[Any]:
    """Execute query and return all rows."""
    async with db.execute(sql, params) as cursor:
        return list(await cursor.fetchall())
```

**IMPORTANT**: These do NOT set `db.row_factory`. They return plain tuples (index-based access), matching the behavior of the private helpers they replace. The existing `execute_query`/`execute_query_one` set `row_factory = aiosqlite.Row` for dict-style access. Do NOT change the existing functions.

Also add time utilities:

```python
from datetime import UTC, datetime

def utc_now() -> datetime:
    """Return current UTC datetime."""
    return datetime.now(UTC)

def to_iso(dt: datetime) -> str:
    """Format datetime as ISO 8601 string with Z suffix."""
    return dt.isoformat(timespec="seconds").replace("+00:00", "Z")

def now_iso() -> str:
    """Return current UTC time as ISO 8601 string."""
    return to_iso(utc_now())
```

### Step 3.2: Update `metrics.py`

1. Remove the local `_scalar` function (lines 24-30)
2. Remove the local `now_iso` function (lines 14-16)
3. Remove the local `_now` function (lines 19-21)
4. Add import: `from observatory_service.services.database import execute_scalar, now_iso, to_iso, utc_now`
5. Replace all calls to `_scalar(` with `execute_scalar(`
6. Replace all calls to `_now()` with `utc_now()`
7. Replace all inline `.isoformat(timespec="seconds").replace("+00:00", "Z")` with `to_iso(...)`

### Step 3.3: Update `agents.py`

1. Remove all three local helper functions (`_scalar`, `_fetchone`, `_fetchall`)
2. Add import: `from observatory_service.services.database import execute_fetchall, execute_fetchone, execute_scalar`
3. Replace all calls to `_scalar(` with `execute_scalar(`, `_fetchone(` with `execute_fetchone(`, `_fetchall(` with `execute_fetchall(`

### Step 3.4: Update `tasks.py`

Same as agents.py:
1. Remove all three local helpers
2. Add import: `from observatory_service.services.database import execute_fetchall, execute_fetchone, execute_scalar`
3. Replace all calls

### Step 3.5: Update `quarterly.py`

1. Remove all three local helpers (`_scalar`, `_fetchone`, `_fetchall`)
2. Remove the local `_now` function
3. Add import: `from observatory_service.services.database import execute_fetchall, execute_fetchone, execute_scalar, utc_now`
4. Replace all calls to `_scalar(` with `execute_scalar(`, etc.
5. Replace all calls to `_now()` with `utc_now()`

### Step 3.6: Do NOT Modify Existing Test Files

Do NOT touch any files in `services/observatory/tests/`. The existing tests are acceptance tests. They must pass as-is.

### Verification for Task 3

```bash
cd /Users/flo/Developer/github/agent-economy/services/observatory && just ci-quiet
```

All existing tests must pass. Zero failures.

---

## Final Verification

After all three tasks are done, run the full project CI:

```bash
cd /Users/flo/Developer/github/agent-economy && just ci-all-quiet
```

This must pass with zero failures across all services.

---

## Rules

- Use `uv run` for all Python execution — never raw `python`, `python3`, or `pip install`
- Do NOT modify any existing test files
- Do NOT add default parameter values for configurable settings
- All config must come from `config.yaml`, not hardcoded
- Commit after each task is complete and verified
