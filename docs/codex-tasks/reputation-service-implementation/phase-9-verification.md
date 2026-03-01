# Phase 9 — Verification and Troubleshooting

## Working Directory

```
services/reputation/
```

---

## Step 1: Run Full CI

```bash
just ci-quiet
```

This is the **only gate that matters**. It runs:

- `ruff check` — linting
- `ruff format --check` — formatting
- `mypy` — type checking (strict mode)
- `pyright` — additional type checking
- `bandit` — security scanning
- `codespell` — spell checking
- `deptry` — dependency checking
- `semgrep` — custom rules
- `pytest` — all tests (unit + integration + architecture)

**The service is complete only when `just ci-quiet` passes with zero failures.**

---

## Step 2: Manual Smoke Test

Start the service and verify basic functionality:

```bash
just run
```

In another terminal:

```bash
# Health check
curl -s http://localhost:8004/health | python3 -m json.tool

# 405 on wrong method
curl -s -X POST http://localhost:8004/health | python3 -m json.tool

# 415 on wrong content type
curl -s -X POST -H "Content-Type: text/plain" -d '{}' http://localhost:8004/feedback | python3 -m json.tool

# 400 on missing token
curl -s -X POST -H "Content-Type: application/json" -d '{"task_id": "t-1"}' http://localhost:8004/feedback | python3 -m json.tool

# Empty task feedback
curl -s http://localhost:8004/feedback/task/t-nonexistent | python3 -m json.tool

# Empty agent feedback
curl -s http://localhost:8004/feedback/agent/a-nonexistent | python3 -m json.tool

# Non-existent feedback ID
curl -s http://localhost:8004/feedback/fb-nonexistent | python3 -m json.tool
```

```bash
just stop  # or Ctrl-C
```

---

## Common Troubleshooting

### Import Errors

- Missing `__init__.py` in `core/`, `routers/`, or `services/` directories
- Circular import between `config.py` and `logging.py` — use lazy import in `logging.py`
- `FeedbackStore` imported at top level in `state.py` — use `TYPE_CHECKING` guard

### Type Checking Failures

- `pyright: ignore[reportUnnecessaryComparison]` needed for `is not None` checks on `Optional` fields that pyright considers already narrowed (e.g., `state.feedback_store is not None` in lifespan shutdown)
- `cast()` needed for Starlette exception handler registration
- `# nosec B105` needed on empty string comparison for bandit (false positive on password check)

### SQLite Issues

- Database file not created → `FeedbackStore.__init__` must call `Path(db_path).parent.mkdir(parents=True, exist_ok=True)`
- `ROLLBACK` after failed transaction → use `contextlib.suppress(sqlite3.Error)` to handle the case where ROLLBACK itself fails
- Thread safety → use `RLock` around all database operations, `check_same_thread=False` on connection

### Middleware Issues

- 415 not returned → verify `_JSON_POST_ENDPOINTS` set contains `("POST", "/feedback")`
- Body not passed through → verify `buffered_receive` correctly replays the full body

### Route Ordering

- `GET /feedback/task/{task_id}` matched as `GET /feedback/{feedback_id}` with `feedback_id="task"` → ensure specific routes are registered before generic routes in the router file

### Test Isolation

- Tests affecting each other → ensure `_clear_caches` fixture resets both settings cache and app state
- Database pollution between tests → use `:memory:` or `tmp_path` for test databases
- Identity client mock not taking effect → verify mock is injected into `state.identity_client` before the request is made

---

## Completion Checklist

- [ ] `just ci-quiet` passes with zero failures
- [ ] All 51 tests from `reputation-service-tests.md` pass
- [ ] All 39 tests from `reputation-service-auth-tests.md` pass
- [ ] Health endpoint returns correct schema with `total_feedback` count
- [ ] Sealed feedback is excluded from all GET queries
- [ ] Mutual reveal works atomically
- [ ] Timeout-based reveal works lazily
- [ ] JWS authentication enforced on POST /feedback
- [ ] GET endpoints remain public
- [ ] Error precedence is correct
- [ ] No internal details leaked in error responses
- [ ] All feedback IDs match `fb-<uuid4>` format
