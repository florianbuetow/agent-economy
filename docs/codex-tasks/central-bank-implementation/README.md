# Central Bank Service — Implementation Instructions

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

## Overview

Implement the Central Bank service (accounts, transactions, escrow) and extend the Identity service with JWS verification. Two-phase build: Phase A extends Identity with `POST /agents/verify-jws` using `joserfc`. Phase B implements the Central Bank following identical patterns to Identity — SQLite persistence, manual JSON parsing, ASGI middleware, and JWS-authenticated endpoints.

**Tech Stack:** Python 3.12, FastAPI, SQLite (WAL mode), joserfc (JWS/EdDSA), httpx (async HTTP client), service-commons (shared infrastructure)

## Files to Read FIRST

Read these before doing anything:

1. `AGENTS.md` — project conventions, architecture, testing rules
2. `docs/specifications/service-api/central-bank-service-specs.md` — the API specification
3. `docs/specifications/service-tests/central-bank-service-tests.md` — the acceptance tests that define pass/fail
4. `docs/service-implementation-guide.md` — file-by-file patterns for all services

## Global Rules

- Use `uv run` for ALL Python execution — never raw `python`, `python3`, or `pip install`
- **Never use default parameter values** for configurable settings
- **All config comes from config.yaml** — no hardcoded values
- Every Pydantic model uses `ConfigDict(extra="forbid")`
- Business logic lives in `services/` — routers are thin wrappers
- Do NOT modify any existing test files in `tests/`
- Do NOT modify files in `libs/service-commons/`

## Implementation Phases

Execute these in order. Each phase has its own file with complete code.

| Phase | File | What It Does |
|-------|------|-------------|
| 1 | `phase-1-identity-jws.md` | Extend Identity service with `POST /agents/verify-jws` (Tasks A1–A4) |
| 2 | `phase-2-config.md` | Central Bank config.yaml, pyproject.toml, config.py (Tasks B1–B2) |
| 3 | `phase-3-foundation.md` | __init__.py, logging.py, schemas.py (Task B3) |
| 4 | `phase-4-core.md` | core/state.py, exceptions.py, middleware.py, __init__.py (Task B4) |
| 5 | `phase-5-clients.md` | services/identity_client.py (Task B5) |
| 6 | `phase-6-service.md` | services/ledger.py, services/__init__.py (Task B6) |
| 7 | `phase-7-routers.md` | routers/health.py, accounts.py, escrow.py, __init__.py (Task B7) |
| 8 | `phase-8-app.md` | core/lifespan.py, app.py — wire everything together (Task B8) |
| 9 | `phase-9-tests.md` | All unit tests + test placeholders for CI (Tasks B9–B13) |
| 10 | `phase-10-verification.md` | Run CI, verify both services, troubleshoot (Task B14) |

## Verification After Each Phase

After completing each phase, run:
```bash
cd services/central-bank && uv run ruff check src/ && uv run ruff format --check src/
```

After Phase 1 (Identity JWS extension), also run:
```bash
cd services/identity && just ci-quiet
```

After Phase 8 (app assembly), also run:
```bash
cd services/central-bank && just run
# In another terminal: curl http://localhost:8002/health
# Then: Ctrl+C
```

After Phase 9 (tests), run:
```bash
cd services/central-bank && just ci-quiet
```

After Phase 10, both services must pass `just ci-quiet`.

## File List (All Files to Create or Modify)

### Identity Service (Phase 1 only)

**Modify:**
- `services/identity/pyproject.toml` — add joserfc dependency
- `services/identity/src/identity_service/services/agent_registry.py` — add `verify_jws` method
- `services/identity/src/identity_service/routers/agents.py` — add endpoint + method-not-allowed
- `services/identity/src/identity_service/core/middleware.py` — whitelist new endpoint

**Create:**
- `services/identity/tests/unit/routers/test_verify_jws.py`

### Central Bank Service (Phases 2–10)

**Modify:**
- `services/central-bank/config.yaml`
- `services/central-bank/pyproject.toml` — add httpx, joserfc, cryptography
- `services/central-bank/src/central_bank_service/__init__.py`
- `services/central-bank/src/central_bank_service/core/__init__.py`
- `services/central-bank/src/central_bank_service/services/__init__.py`
- `services/central-bank/src/central_bank_service/routers/__init__.py`

**Create:**
- `services/central-bank/src/central_bank_service/config.py`
- `services/central-bank/src/central_bank_service/logging.py`
- `services/central-bank/src/central_bank_service/schemas.py`
- `services/central-bank/src/central_bank_service/app.py`
- `services/central-bank/src/central_bank_service/core/state.py`
- `services/central-bank/src/central_bank_service/core/exceptions.py`
- `services/central-bank/src/central_bank_service/core/middleware.py`
- `services/central-bank/src/central_bank_service/core/lifespan.py`
- `services/central-bank/src/central_bank_service/services/identity_client.py`
- `services/central-bank/src/central_bank_service/services/ledger.py`
- `services/central-bank/src/central_bank_service/routers/health.py`
- `services/central-bank/src/central_bank_service/routers/accounts.py`
- `services/central-bank/src/central_bank_service/routers/escrow.py`
- `services/central-bank/tests/conftest.py`
- `services/central-bank/tests/unit/conftest.py`
- `services/central-bank/tests/unit/test_config.py`
- `services/central-bank/tests/unit/routers/__init__.py`
- `services/central-bank/tests/unit/routers/conftest.py`
- `services/central-bank/tests/unit/routers/test_health.py`
- `services/central-bank/tests/unit/routers/test_accounts.py`
- `services/central-bank/tests/unit/routers/test_escrow.py`
- `services/central-bank/tests/integration/conftest.py`
- `services/central-bank/tests/integration/test_endpoints.py`
- `services/central-bank/tests/performance/conftest.py`
- `services/central-bank/tests/performance/test_performance.py`
