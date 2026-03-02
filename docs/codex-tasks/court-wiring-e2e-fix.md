# Court E2E Wiring Fix — Codex Task

> Read AGENTS.md FIRST for project conventions.
> Use `uv run` for all Python execution. Never use raw python or pip install.
> Do NOT modify any existing test files.
> Commit after each phase.

## Context

Three Court service ruling side-effects need end-to-end verification:
1. Escrow split via Central Bank (already wired — verify only)
2. Ruling recording on TaskBoard (**BUG: missing `task_id` in JWS payload**)
3. Reputation feedback posting (already wired — verify only)

The only code change is a one-line bug fix in the ruling orchestrator.

## Files to Read FIRST

Read these files in order before doing anything:

1. `AGENTS.md` — project conventions
2. `services/court/src/court_service/services/ruling_orchestrator.py` — THE FILE WITH THE BUG
3. `services/court/src/court_service/services/task_board_client.py` — how rulings are sent to TaskBoard
4. `services/task-board/src/task_board_service/services/task_manager.py` — TaskBoard's record_ruling validation (search for `record_ruling`)
5. `docs/specifications/service-api/task-board-service-specs.md` — search for "record_ruling" to see required payload

After reading ALL files, execute Phase 1 through Phase 3 in order.

=== PHASE 1: Fix the missing task_id bug ===

**File:** `services/court/src/court_service/services/ruling_orchestrator.py`

**Find the `_record_task_ruling` method** (around line 235-269). Inside it, there is a dict
that is passed to `task_board_client.record_ruling()`. It currently looks like this:

```python
await task_board_client.record_ruling(
    str(dispute["task_id"]),
    {
        "action": "record_ruling",
        "ruling_id": dispute_id,
        "worker_pct": median_worker_pct,
        "ruling_summary": ruling_summary,
    },
)
```

The TaskBoard's `record_ruling` endpoint at `task_manager.py` **requires** `task_id` in the
JWS payload and validates it matches the URL path parameter. The Court omits it, causing a
400 INVALID_PAYLOAD error.

**The fix:** Add `"task_id": str(dispute["task_id"])` to the payload dict:

```python
await task_board_client.record_ruling(
    str(dispute["task_id"]),
    {
        "action": "record_ruling",
        "task_id": str(dispute["task_id"]),
        "ruling_id": dispute_id,
        "worker_pct": median_worker_pct,
        "ruling_summary": ruling_summary,
    },
)
```

This is the ONLY change. Do NOT modify any other file.

### Verification (Phase 1):

Run the court service CI checks:
```bash
cd services/court && just ci-quiet
```

All checks must pass. If any fail, fix them before proceeding.

=== PHASE 2: Verify existing unit tests still pass ===

Run the court unit tests specifically:
```bash
cd services/court && uv run pytest tests/unit/ -v --tb=short 2>&1 | tail -30
```

All unit tests should pass. The mock `_TaskBoardClientMock` in the unit tests accepts
any payload, so the unit tests will pass regardless. But they must still pass to
confirm nothing else broke.

=== PHASE 3: Commit ===

Stage and commit:
```bash
git add services/court/src/court_service/services/ruling_orchestrator.py
git commit -m "fix(court): add missing task_id to ruling payload sent to TaskBoard

The RulingOrchestrator._record_task_ruling() omitted task_id from the
JWS payload. The TaskBoard's record_ruling endpoint requires task_id
in the payload and validates it matches the URL path parameter, causing
a 400 INVALID_PAYLOAD error on every ruling attempt.

Fixes: agent-economy-98r.12"
```

Do NOT push to remote. Only commit locally.

## Summary of Deliverables

1. One-line fix in `services/court/src/court_service/services/ruling_orchestrator.py`
   — add `"task_id": str(dispute["task_id"])` to the payload dict in `_record_task_ruling`
