# INSTRUCTIONS FOR CODEX AGENT

Read these files FIRST before doing anything:
1. AGENTS.md — project conventions
2. docs/codex-tasks/2026-03-02-full-ticket-implementation.md — THE PLAN
3. services/ui/src/ui_service/schemas.py — current Pydantic models
4. services/ui/src/ui_service/services/metrics.py — current metrics logic
5. services/ui/src/ui_service/services/tasks.py — current tasks logic
6. services/ui/src/ui_service/routers/metrics.py — metrics route handlers
7. services/ui/src/ui_service/routers/tasks.py — tasks route handlers
8. services/ui/tests/integration/test_metrics_delta.py — failing tests for deltas
9. services/ui/tests/integration/test_task_list.py — failing tests for task list
10. services/ui/tests/integration/test_ticker_deltas.py — failing tests for ticker

After reading ALL files, execute TIER 1 from the plan:

TIER 1 has two tickets:
- Ticket 1A: Add delta fields to GET /api/metrics (modify schemas.py + metrics.py)
- Ticket 1B: Add GET /api/tasks endpoint (modify schemas.py + tasks.py + routers/tasks.py)

RULES:
- NO GIT. Do not use any git commands. Just edit files.
- Use `uv run` for Python. NEVER python/python3/pip.
- Do NOT modify existing test files.
- All Pydantic models use ConfigDict(extra="forbid").
- GET /api/tasks route MUST be BEFORE GET /api/tasks/{task_id} in the router.
- After 1A: cd services/ui && uv run pytest tests/integration/test_metrics_delta.py tests/integration/test_ticker_deltas.py -v
- After 1B: cd services/ui && uv run pytest tests/integration/test_task_list.py -v
- After BOTH: cd services/ui && just ci-quiet

Follow the plan exactly. Do not improvise. START by reading the files listed above.
