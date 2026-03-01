# Phase 6 — Dispute Service (Business Logic)

## Working Directory

```
services/court/
```

This is the most complex phase. The `DisputeService` manages the SQLite store, the dispute lifecycle state machine, and the ruling orchestration including atomic rollback.

---

## File 1: `src/court_service/services/dispute_service.py`

Pure Python — no FastAPI imports. This file contains all business logic for dispute management.

### SQLite Schema

Three tables:

**`disputes`**:
```sql
CREATE TABLE disputes (
    dispute_id TEXT PRIMARY KEY,
    task_id TEXT NOT NULL UNIQUE,
    claimant_id TEXT NOT NULL,
    respondent_id TEXT NOT NULL,
    claim TEXT NOT NULL,
    rebuttal TEXT,
    status TEXT NOT NULL DEFAULT 'rebuttal_pending',
    rebuttal_deadline TEXT NOT NULL,
    worker_pct INTEGER,
    ruling_summary TEXT,
    escrow_id TEXT NOT NULL,
    filed_at TEXT NOT NULL,
    rebutted_at TEXT,
    ruled_at TEXT
);
```

**`votes`**:
```sql
CREATE TABLE votes (
    vote_id TEXT PRIMARY KEY,
    dispute_id TEXT NOT NULL REFERENCES disputes(dispute_id),
    judge_id TEXT NOT NULL,
    worker_pct INTEGER NOT NULL,
    reasoning TEXT NOT NULL,
    voted_at TEXT NOT NULL,
    UNIQUE(dispute_id, judge_id)
);
```

The `task_id` UNIQUE constraint on `disputes` enforces one dispute per task. The `(dispute_id, judge_id)` UNIQUE constraint on `votes` enforces one vote per judge per dispute.

### Initialization

- Open SQLite connection with WAL mode, foreign keys enabled, 5-second busy timeout
- Create tables if not exists
- Use `threading.RLock` for thread safety (same pattern as Central Bank's Ledger)

### Method: `file_dispute(...) -> dict`

Creates a new dispute record.

Parameters: `task_id`, `claimant_id`, `respondent_id`, `claim`, `escrow_id`, `rebuttal_deadline_seconds`

Logic:
1. Generate `dispute_id` = `"disp-" + uuid4()`
2. Calculate `rebuttal_deadline` = `filed_at + rebuttal_deadline_seconds`
3. INSERT into `disputes` table
4. On `IntegrityError` (duplicate `task_id`) → raise `ServiceError("DISPUTE_ALREADY_EXISTS", ..., 409)`
5. Return the full dispute dict with `votes: []`

### Method: `submit_rebuttal(dispute_id: str, rebuttal: str) -> dict`

Parameters: `dispute_id`, `rebuttal`

Logic:
1. Fetch dispute by `dispute_id` → raise `ServiceError("DISPUTE_NOT_FOUND", ..., 404)` if missing
2. Check `status == "rebuttal_pending"` → raise `ServiceError("INVALID_DISPUTE_STATUS", ..., 409)` if not
3. Check `rebuttal IS NULL` → raise `ServiceError("REBUTTAL_ALREADY_SUBMITTED", ..., 409)` if already set
4. UPDATE: set `rebuttal`, `rebutted_at` = now
5. Status remains `"rebuttal_pending"` — the platform triggers ruling separately
6. Return updated dispute dict with `votes: []`

### Method: `execute_ruling(dispute_id: str, judges: list[Judge], task_data: dict, task_board_client, central_bank_client, reputation_client) -> dict`

This is the complex orchestration method. It coordinates the judge panel, side effects, and rollback.

Parameters: `dispute_id`, `judges` (the panel), `task_data` (from Task Board), plus the three downstream clients

Logic:
1. Fetch dispute → raise `DISPUTE_NOT_FOUND` if missing
2. Validate `status == "rebuttal_pending"` → raise `INVALID_DISPUTE_STATUS` if not
3. Validate not already ruled → raise `DISPUTE_ALREADY_RULED` if `ruled_at is not None`
4. **Transition to `"judging"`** — UPDATE status in DB
5. Build `DisputeContext` from dispute + `task_data`:
   - `task_spec` = `task_data["spec"]`
   - `deliverables` = `task_data.get("deliverables", [])`
   - `claim` = dispute's claim
   - `rebuttal` = dispute's rebuttal (may be None)
   - `task_title` = `task_data["title"]`
   - `reward` = `task_data["reward"]`
6. **Call each judge sequentially**:
   ```python
   votes = []
   for judge in judges:
       vote = await judge.evaluate(context)
       votes.append(vote)
   ```
   If any judge raises → rollback to `"rebuttal_pending"`, raise `ServiceError("JUDGE_UNAVAILABLE", ..., 502)`
7. **Calculate median `worker_pct`**:
   ```python
   sorted_pcts = sorted(v.worker_pct for v in votes)
   median_pct = sorted_pcts[len(sorted_pcts) // 2]
   ```
8. **Compose `ruling_summary`** from all judge reasonings (e.g., join with separator)
9. **Execute post-ruling side effects** (all must succeed):
   a. `central_bank_client.split_escrow(escrow_id, respondent_id, claimant_id, median_pct)`
   b. `reputation_client.submit_feedback(...)` — spec quality feedback for the poster (claimant)
   c. `reputation_client.submit_feedback(...)` — delivery quality feedback for the worker (respondent)
   d. `task_board_client.record_ruling(task_id, {...})`
   If any of these fail → rollback: revert status to `"rebuttal_pending"`, delete any persisted votes, raise the appropriate `502` error
10. **Persist ruling**: UPDATE dispute with `status="ruled"`, `worker_pct`, `ruling_summary`, `ruled_at`; INSERT all votes
11. Return the full dispute dict with populated `votes` array

### Rollback Behavior

The atomicity guarantee: if ANY step after transitioning to `"judging"` fails, the dispute reverts to `"rebuttal_pending"` and no votes are persisted. Use a try/except around steps 6–10.

```python
try:
    # steps 6-10
except ServiceError:
    # Rollback: revert status to rebuttal_pending
    self._revert_to_rebuttal_pending(dispute_id)
    raise
```

### Method: `get_dispute(dispute_id: str) -> dict | None`

Fetch a single dispute with its votes. Returns `None` if not found.

### Method: `list_disputes(task_id: str | None, status: str | None) -> list[dict]`

List disputes with optional filters. Filters are ANDed. Unknown filter values return empty list (no error). Returns summary dicts (no claim, rebuttal, ruling_summary, or votes).

### Helper Methods

- `count_disputes() -> int` — total dispute count (for health endpoint)
- `count_active() -> int` — disputes where `status != "ruled"` (for health endpoint)
- `close()` — close SQLite connection

### Feedback Payload Structure

When calling the Reputation service after ruling, the Court submits feedback as the platform agent:

**Spec quality feedback** (for the poster/claimant):
```python
{
    "action": "submit_feedback",
    "task_id": task_id,
    "from_agent_id": platform_agent_id,
    "to_agent_id": claimant_id,
    "category": "spec_quality",
    "rating": <derived from worker_pct>,
    "comment": ruling_summary,
}
```

**Delivery quality feedback** (for the worker/respondent):
```python
{
    "action": "submit_feedback",
    "task_id": task_id,
    "from_agent_id": platform_agent_id,
    "to_agent_id": respondent_id,
    "category": "delivery_quality",
    "rating": <derived from worker_pct>,
    "comment": ruling_summary,
}
```

Rating derivation from `worker_pct`:
- `worker_pct >= 80` → `"extremely_satisfied"` (spec was clear, worker delivered)
- `worker_pct >= 40` → `"satisfied"` (mixed result)
- `worker_pct < 40` → `"dissatisfied"` (spec was unclear OR worker underdelivered)

Note: for spec quality, the rating logic is inverted — high `worker_pct` means the spec was ambiguous (favored the worker), so spec quality rating should be lower. For delivery quality, high `worker_pct` means the worker delivered well.

**Spec quality rating** (poster):
- `worker_pct >= 80` → `"dissatisfied"` (spec was too vague, ruling favored worker)
- `worker_pct >= 40` → `"satisfied"` (mixed)
- `worker_pct < 40` → `"extremely_satisfied"` (spec was clear, poster was right)

**Delivery quality rating** (worker):
- `worker_pct >= 80` → `"extremely_satisfied"` (worker delivered well)
- `worker_pct >= 40` → `"satisfied"` (mixed)
- `worker_pct < 40` → `"dissatisfied"` (worker underdelivered)

---

## File 2: `src/court_service/services/__init__.py`

Export the public interface:

```python
from court_service.services.dispute_service import DisputeService

__all__ = ["DisputeService"]
```

Also export the client classes if desired, or let them be imported directly.

---

## Verification

```bash
uv run ruff check src/ && uv run ruff format --check src/
uv run python -c "from court_service.services.dispute_service import DisputeService; print('OK')"
```
