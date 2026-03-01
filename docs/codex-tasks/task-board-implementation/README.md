# Task Board Service — Implementation Instructions

## Overview

Implement the Task Board service from its existing scaffolding. The service manages the full lifecycle of tasks — from creation and bidding through execution, delivery, and review. It orchestrates escrow operations with the Central Bank and delegates authentication to the Identity service.

The Task Board is the most complex service in the system: 15 endpoints, JWS authentication, escrow integration, sealed bids, deadline evaluation, asset storage, and a state machine with 8 statuses and platform-signed operations.

## Files to Read FIRST

Read these before doing anything:

1. `AGENTS.md` — project conventions, architecture, testing rules
2. `docs/specifications/service-api/task-board-service-specs.md` — the API specification (endpoints, data model, lifecycle)
3. `docs/specifications/service-api/task-board-service-auth-specs.md` — authentication and authorization specification
4. `docs/specifications/service-tests/task-board-service-tests.md` — the 171 acceptance tests that define pass/fail
5. `docs/service-implementation-guide.md` — file-by-file patterns for all services

## Global Rules

- Use `uv run` for ALL Python execution — never raw `python`, `python3`, or `pip install`
- **Never use default parameter values** for configurable settings
- **All config comes from config.yaml** — no hardcoded values
- Every Pydantic model uses `ConfigDict(extra="forbid")`
- Business logic lives in `services/` — routers are thin wrappers
- Do NOT modify any existing test files in `tests/`
- Do NOT modify files in `libs/service-commons/`
- Working directory for all commands: `services/task-board/`

## Implementation Phases

Execute these in order. Each phase has its own file with complete instructions.

| Phase | File | What It Does |
|-------|------|-------------|
| 1 | `phase-1-config.md` | Add dependencies, extend config.yaml |
| 2 | `phase-2-foundation.md` | config.py, __init__.py, logging.py, schemas.py |
| 3 | `phase-3-core.md` | core/state.py, exceptions.py, middleware.py, __init__.py |
| 4 | `phase-4-clients.md` | IdentityClient, CentralBankClient, PlatformSigner |
| 5 | `phase-5-service.md` | services/task_manager.py — all business logic |
| 6 | `phase-6-routers.md` | routers/health.py, tasks.py, bids.py, assets.py |
| 7 | `phase-7-lifespan-app.md` | core/lifespan.py, app.py — wire everything |
| 8 | `phase-8-tests.md` | Unit tests + integration test placeholders |
| 9 | `phase-9-verification.md` | Run CI, troubleshoot common issues |

## Verification After Each Phase

After completing each phase, run:
```bash
cd services/task-board && uv run ruff check src/ && uv run ruff format --check src/
```

After Phase 7 (app assembly), also run:
```bash
cd services/task-board && just run
# In another terminal: curl http://localhost:8003/health
# Then: just kill
```

After Phase 8 (tests), run:
```bash
cd services/task-board && just ci-quiet
```

## File List (All Files to Create or Modify)

### Modify Existing Files
- `pyproject.toml` — add `cryptography`, `httpx`, `joserfc`, `aiofiles`, `python-multipart` dependencies
- `config.yaml` — add database, identity, central_bank, platform, assets, request sections

### Create New Source Files
- `src/task_board_service/__init__.py` — overwrite with version
- `src/task_board_service/app.py`
- `src/task_board_service/config.py`
- `src/task_board_service/logging.py`
- `src/task_board_service/schemas.py`
- `src/task_board_service/core/__init__.py` — overwrite with re-exports
- `src/task_board_service/core/state.py`
- `src/task_board_service/core/exceptions.py`
- `src/task_board_service/core/middleware.py`
- `src/task_board_service/core/lifespan.py`
- `src/task_board_service/clients/__init__.py`
- `src/task_board_service/clients/identity_client.py`
- `src/task_board_service/clients/central_bank_client.py`
- `src/task_board_service/clients/platform_signer.py`
- `src/task_board_service/services/__init__.py` — overwrite with re-exports
- `src/task_board_service/services/task_manager.py`
- `src/task_board_service/routers/__init__.py` — overwrite with re-exports
- `src/task_board_service/routers/health.py`
- `src/task_board_service/routers/tasks.py`
- `src/task_board_service/routers/bids.py`
- `src/task_board_service/routers/assets.py`

### Create New Test Files
- `tests/conftest.py`
- `tests/unit/conftest.py`
- `tests/unit/test_config.py`
- `tests/unit/routers/__init__.py`
- `tests/unit/routers/conftest.py`
- `tests/unit/routers/test_health.py`
- `tests/unit/routers/test_tasks.py`
- `tests/unit/routers/test_bids.py`
- `tests/unit/routers/test_assets.py`
- `tests/unit/routers/test_lifecycle.py`
- `tests/unit/routers/test_rulings.py`
- `tests/unit/routers/test_error_precedence.py`
- `tests/unit/routers/test_security.py`
- `tests/unit/test_task_manager.py`
- `tests/unit/test_clients.py`
- `tests/integration/conftest.py`
- `tests/integration/test_endpoints.py`
- `tests/performance/conftest.py`
- `tests/performance/test_performance.py`

### Create New Directories
- `src/task_board_service/clients/`

## Architecture Overview

```
                           ┌─────────────────┐
                           │     app.py       │
                           │  create_app()    │
                           └────────┬────────┘
                                    │
                    ┌───────────────┼───────────────┐
                    │               │               │
              ┌─────┴─────┐  ┌─────┴─────┐  ┌─────┴─────┐
              │  routers/  │  │  core/     │  │middleware  │
              │  tasks.py  │  │  state.py  │  │           │
              │  bids.py   │  │  lifespan  │  │ContentType│
              │  assets.py │  │  except.   │  │BodySize   │
              │  health.py │  │           │  │           │
              └─────┬─────┘  └───────────┘  └───────────┘
                    │
              ┌─────┴──────┐
              │  services/  │
              │ task_mgr.py │
              └─────┬──────┘
                    │
         ┌──────────┼──────────┐
         │          │          │
   ┌─────┴─────┐ ┌─┴───┐ ┌───┴───┐
   │ clients/  │ │SQLite│ │FS     │
   │ identity  │ │      │ │assets │
   │ bank      │ │      │ │       │
   │ signer    │ │      │ │       │
   └───────────┘ └──────┘ └───────┘
```

### Layer Responsibilities

- **Routers**: Parse requests, extract tokens, call service layer, format responses. Thin wrappers only.
- **Services (task_manager.py)**: All business logic — task creation, bidding, acceptance, submission, approval, dispute, ruling, deadline evaluation, state machine transitions. No FastAPI imports.
- **Clients**: HTTP clients for Identity service and Central Bank. Platform signer for creating outgoing JWS tokens.
- **Core**: Application state, exception handlers, request validation middleware, lifecycle management.

### Key Design Decisions

1. **Manual JSON parsing** (not Pydantic model binding) in routers — to return `400 INVALID_JSON`, `400 MISSING_FIELD`, etc. instead of FastAPI's default `422`.
2. **JWS verification** delegated to Identity service via `POST /agents/verify-jws` — the Task Board never touches Ed25519 directly for incoming tokens.
3. **Platform signing** uses `joserfc` to create JWS compact tokens for outgoing escrow operations.
4. **Lazy deadline evaluation** on every read — no background jobs. Atomic via database transaction.
5. **Escrow rollback** on task creation failure — if DB insert fails after escrow lock, release escrow back.
6. **Sealed bids** — conditional authentication on `GET /bids` based on task status.
7. **Asset storage** on filesystem — `{storage_path}/{task_id}/{asset_id}/{filename}`.
