# Database Gateway Service — Implementation Instructions

## Overview

Implement the Database Gateway service from scratch (no existing scaffolding). The service is a thin write serialization layer that owns the shared `economy.db` SQLite database. It receives structured write requests from other services, executes them atomically within `BEGIN IMMEDIATE` transactions, and pairs every write with an event INSERT. It contains no business logic, no authentication, and no outbound calls.

The gateway is the simplest service architecturally — a leaf service with no clients, no auth, no state machine logic. Its complexity lies in the breadth of endpoints (15) and the correctness of transaction handling.

## Files to Read FIRST

Read these before doing anything:

1. `AGENTS.md` — project conventions, architecture, testing rules
2. `docs/specifications/service-api/db-gateway-service-specs.md` — the API specification (15 endpoints, transaction SQL, error codes)
3. `docs/specifications/service-tests/db-gateway-service-tests.md` — the 178 acceptance tests that define pass/fail
4. `docs/specifications/schema.sql` — the unified SQLite schema (all tables, indexes, constraints)
5. `docs/service-implementation-guide.md` — file-by-file patterns for all services

## Global Rules

- Use `uv run` for ALL Python execution — never raw `python`, `python3`, or `pip install`
- **Never use default parameter values** for configurable settings
- **All config comes from config.yaml** — no hardcoded values
- Every Pydantic model uses `ConfigDict(extra="forbid")`
- Business logic lives in `services/` — routers are thin wrappers
- Do NOT modify any existing test files in `tests/`
- Do NOT modify files in `libs/service-commons/`
- Working directory for all commands: `services/db-gateway/`

## Implementation Phases

Execute these in order. Each phase has its own file with complete code.

| Phase | File | What It Does |
|-------|------|-------------|
| 1 | `phase-1-scaffolding.md` | Create directory structure, pyproject.toml, config.yaml, justfile, Dockerfile |
| 2 | `phase-2-foundation.md` | config.py, __init__.py, logging.py, schemas.py |
| 3 | `phase-3-core.md` | core/state.py, exceptions.py, middleware.py, __init__.py, lifespan.py |
| 4 | `phase-4-service.md` | services/db_writer.py — SQLite transaction executor |
| 5 | `phase-5-routers-health-identity-bank.md` | routers/helpers.py, health.py, identity.py, bank.py |
| 6 | `phase-6-routers-board-reputation-court.md` | routers/board.py, reputation.py, court.py, routers/__init__.py |
| 7 | `phase-7-app.md` | app.py — wire everything together |
| 8 | `phase-8-tests.md` | Unit tests + integration test placeholders |
| 9 | `phase-9-verification.md` | Run CI, troubleshoot common issues |

## Verification After Each Phase

After completing each phase, run:
```bash
cd services/db-gateway && uv run ruff check src/ && uv run ruff format --check src/
```

After Phase 7 (app assembly), also run:
```bash
cd services/db-gateway && just run
# In another terminal: curl http://localhost:8006/health
# Then: just kill
```

After Phase 8 (tests), run:
```bash
cd services/db-gateway && just ci-quiet
```

## File List (All Files to Create)

### Scaffolding Files
- `services/db-gateway/pyproject.toml`
- `services/db-gateway/config.yaml`
- `services/db-gateway/justfile`
- `services/db-gateway/pyrightconfig.json`
- `services/db-gateway/Dockerfile`

### Source Files
- `src/db_gateway_service/__init__.py`
- `src/db_gateway_service/app.py`
- `src/db_gateway_service/config.py`
- `src/db_gateway_service/logging.py`
- `src/db_gateway_service/schemas.py`
- `src/db_gateway_service/core/__init__.py`
- `src/db_gateway_service/core/state.py`
- `src/db_gateway_service/core/exceptions.py`
- `src/db_gateway_service/core/middleware.py`
- `src/db_gateway_service/core/lifespan.py`
- `src/db_gateway_service/services/__init__.py`
- `src/db_gateway_service/services/db_writer.py`
- `src/db_gateway_service/routers/__init__.py`
- `src/db_gateway_service/routers/helpers.py`
- `src/db_gateway_service/routers/health.py`
- `src/db_gateway_service/routers/identity.py`
- `src/db_gateway_service/routers/bank.py`
- `src/db_gateway_service/routers/board.py`
- `src/db_gateway_service/routers/reputation.py`
- `src/db_gateway_service/routers/court.py`

### Test Files
- `tests/conftest.py`
- `tests/unit/conftest.py`
- `tests/unit/test_config.py`
- `tests/unit/test_db_writer.py`
- `tests/unit/routers/__init__.py`
- `tests/unit/routers/conftest.py`
- `tests/unit/routers/test_health.py`
- `tests/unit/routers/test_identity.py`
- `tests/unit/routers/test_bank.py`
- `tests/unit/routers/test_board.py`
- `tests/unit/routers/test_reputation.py`
- `tests/unit/routers/test_court.py`
- `tests/integration/conftest.py`
- `tests/integration/test_endpoints.py`
- `tests/performance/conftest.py`
- `tests/performance/test_performance.py`

## Architecture Overview

```
                           ┌─────────────────┐
                           │     app.py       │
                           │  create_app()    │
                           └────────┬────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────┴──────┐ ┌─────┴─────┐  ┌─────┴─────┐
              │  routers/   │ │  core/     │  │middleware  │
              │  health.py  │ │  state.py  │  │           │
              │  identity.py│ │  lifespan  │  │ContentType│
              │  bank.py    │ │  except.   │  │BodySize   │
              │  board.py   │ │            │  │           │
              │  reputation │ └───────────┘  └───────────┘
              │  court.py   │
              └─────┬──────┘
                    │
              ┌─────┴──────┐
              │  services/  │
              │ db_writer.py│
              └─────┬──────┘
                    │
              ┌─────┴─────┐
              │  SQLite    │
              │ economy.db │
              └───────────┘
```

### Layer Responsibilities

- **Routers**: Parse JSON body, validate required fields, call service layer, format responses. Thin wrappers only.
- **Services (db_writer.py)**: All SQLite transaction logic — opens connection, executes `BEGIN IMMEDIATE` transactions, handles idempotency via UNIQUE constraint detection, inserts events atomically. No FastAPI imports.
- **Core**: Application state, exception handlers, request validation middleware, lifecycle management.

### Key Design Decisions

1. **Manual JSON parsing** (not Pydantic model binding) in routers — to return `400 MISSING_FIELD`, `400 INVALID_FIELD_TYPE`, etc. instead of FastAPI's default `422`.
2. **No clients** — leaf service, no outbound HTTP calls.
3. **No authentication** — trusts internal callers.
4. **Shared schema** — does not create tables. Assumes `economy.db` already has the schema from `schema.sql`. The lifespan initializes the schema from the file at startup.
5. **`BEGIN IMMEDIATE`** for all writes — prevents deadlocks by acquiring write lock upfront.
6. **Single `DbWriter` class** with one method per endpoint — auditable, testable, maps 1:1 to API spec.
7. **Shared router helpers** in `routers/helpers.py` — JSON parsing, field validation, event validation reused across all domain routers.
8. **Dynamic SET clause** in task status update — builds SQL from allowed column whitelist.
