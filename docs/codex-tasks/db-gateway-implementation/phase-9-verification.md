# Phase 9 — Verification

## Working Directory

All commands run from `services/db-gateway/`.

---

## Step 1: Run Full CI

```bash
cd services/db-gateway && just ci-quiet
```

This runs the full CI pipeline:
1. `init` — install dependencies
2. `code-format` — auto-fix formatting
3. `code-style` — lint check (ruff)
4. `code-typecheck` — mypy strict mode
5. `code-security` — bandit
6. `code-deptry` — dependency hygiene
7. `code-spell` — spelling
8. `code-semgrep` — custom rules
9. `code-audit` — vulnerability scan
10. `test-unit` — unit tests
11. `code-lspchecks` — pyright strict mode

**Every check must pass with zero failures.**

---

## Step 2: Start the Service and Smoke Test

```bash
cd services/db-gateway && just run
```

In another terminal:

```bash
# Health check
curl -s http://localhost:8006/health | jq .

# Register an agent
curl -s -X POST http://localhost:8006/identity/agents \
  -H "Content-Type: application/json" \
  -d '{
    "agent_id": "a-smoke-test",
    "name": "Smoke Test Agent",
    "public_key": "ed25519:c21tb2tl",
    "registered_at": "2026-02-28T10:00:00Z",
    "event": {
      "event_source": "identity",
      "event_type": "agent.registered",
      "timestamp": "2026-02-28T10:00:00Z",
      "task_id": null,
      "agent_id": "a-smoke-test",
      "summary": "Smoke test agent registered",
      "payload": "{\"agent_name\": \"Smoke Test Agent\"}"
    }
  }' | jq .

# Verify event count increased
curl -s http://localhost:8006/health | jq '.total_events'
```

Expected:
- Health returns `200` with `"status": "ok"`
- Agent registration returns `201` with `agent_id` and `event_id`
- Event count should be `1`

Then stop the service:

```bash
cd services/db-gateway && just kill
```

---

## Common Issues and Fixes

### Issue: `ModuleNotFoundError: No module named 'db_gateway_service'`

**Cause**: `pyproject.toml` wheel packages path is wrong.

**Fix**: Ensure `[tool.hatch.build.targets.wheel]` has:
```toml
packages = ["src/db_gateway_service"]
```

### Issue: `sqlite3.OperationalError: table already exists`

**Cause**: `schema.sql` uses `CREATE TABLE` not `CREATE TABLE IF NOT EXISTS`.

**Fix**: The `DbWriter._init_schema()` wraps `executescript()` in a try/except that silently ignores `OperationalError`. This is correct — the schema is already created.

### Issue: `mypy` reports missing type stubs

**Cause**: Some dependencies lack type stubs.

**Fix**: Add a `[[tool.mypy.overrides]]` section for the specific module:
```toml
[[tool.mypy.overrides]]
module = "service_commons.*"
ignore_missing_imports = true
```

### Issue: `pyright` reports unknown types from service_commons

**Cause**: `service_commons` doesn't ship `py.typed` marker.

**Fix**: Already handled by `pyrightconfig.json` settings (`reportUnknownMemberType = false`, etc.).

### Issue: Tests fail with `RuntimeError: Application state not initialized`

**Cause**: Test fixture doesn't initialize app state before creating the test client.

**Fix**: Ensure the `app_with_writer` fixture calls `init_app_state()` and sets `state.db_writer` before yielding the client. The lifespan creates a new state, so you may need to re-inject the writer after the client is created.

### Issue: `codespell` flags a word in SQL or domain terms

**Fix**: Add the word to `../../config/codespell/ignore.txt`.

### Issue: `bandit` flags `sqlite3.connect` (B608)

**Cause**: Bandit warns about SQLite string formatting.

**Fix**: The `noqa: S608` comment on the dynamic SQL in `update_task_status` suppresses this. The column names are validated against a whitelist, so this is safe.

### Issue: `ruff` flags unused imports

**Fix**: Run `just code-format` to auto-fix, or remove the unused imports manually.

---

## Definition of Done

The service is complete when:

1. `just ci-quiet` passes with zero failures
2. `just run` starts the service on port 8006
3. `GET /health` returns `200` with `status: "ok"`, `database_size_bytes`, and `total_events`
4. All 14 POST endpoints accept valid requests and return the correct status codes
5. Events are atomically paired with every write
6. The 178 acceptance tests from the test specification can be written against this implementation
