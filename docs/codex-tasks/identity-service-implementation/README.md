# Identity Service — Implementation Instructions

## Overview

Implement the Identity & PKI service from its existing scaffolding. The service handles agent registration with Ed25519 public keys, signature verification, agent lookup, and listing. It uses SQLite for storage.

## Files to Read FIRST

Read these before doing anything:

1. `AGENTS.md` — project conventions, architecture, testing rules
2. `docs/specifications/service-api/identity-service-specs.md` — the API specification
3. `docs/specifications/service-tests/identity-service-tests.md` — the 48 acceptance tests that define pass/fail
4. `docs/service-implementation-guide.md` — file-by-file patterns for all services

## Global Rules

- Use `uv run` for ALL Python execution — never raw `python`, `python3`, or `pip install`
- **Never use default parameter values** for configurable settings
- **All config comes from config.yaml** — no hardcoded values
- Every Pydantic model uses `ConfigDict(extra="forbid")`
- Business logic lives in `services/` — routers are thin wrappers
- Do NOT modify any existing test files in `tests/`
- Do NOT modify files in `libs/service-commons/`
- Working directory for all commands: `services/identity/`

## Implementation Phases

Execute these in order. Each phase has its own file with complete code.

| Phase | File | What It Does |
|-------|------|-------------|
| 1 | `phase-1-config.md` | Add dependencies, extend config.yaml |
| 2 | `phase-2-foundation.md` | config.py, __init__.py, logging.py, schemas.py |
| 3 | `phase-3-core.md` | core/state.py, exceptions.py, middleware.py, __init__.py, lifespan.py |
| 4 | `phase-4-service.md` | services/agent_registry.py, services/__init__.py |
| 5 | `phase-5-routers.md` | routers/health.py, routers/agents.py, routers/__init__.py |
| 6 | `phase-6-app.md` | app.py — wire everything together |
| 7 | `phase-7-tests.md` | Unit tests + test placeholders for CI |
| 8 | `phase-8-verification.md` | Run CI, run acceptance tests, troubleshoot |

## Verification After Each Phase

After completing each phase, run:
```bash
cd services/identity && uv run ruff check src/ && uv run ruff format --check src/
```

After Phase 6 (app assembly), also run:
```bash
cd services/identity && just run
# In another terminal: curl http://localhost:8001/health
# Then: just kill
```

After Phase 7 (tests), run:
```bash
cd services/identity && just ci-quiet
```

After Phase 8, run the acceptance tests against the live service.

## File List (All Files to Create or Modify)

### Modify Existing Files
- `pyproject.toml` — add `cryptography` dependency
- `config.yaml` — add database, crypto, request sections

### Create New Source Files
- `src/identity_service/__init__.py` — overwrite with version
- `src/identity_service/app.py`
- `src/identity_service/config.py`
- `src/identity_service/logging.py`
- `src/identity_service/schemas.py`
- `src/identity_service/core/__init__.py` — overwrite with re-exports
- `src/identity_service/core/state.py`
- `src/identity_service/core/exceptions.py`
- `src/identity_service/core/middleware.py`
- `src/identity_service/core/lifespan.py`
- `src/identity_service/services/__init__.py` — overwrite with re-exports
- `src/identity_service/services/agent_registry.py`
- `src/identity_service/routers/__init__.py` — overwrite with re-exports
- `src/identity_service/routers/health.py`
- `src/identity_service/routers/agents.py`

### Create New Test Files
- `tests/conftest.py`
- `tests/unit/conftest.py`
- `tests/unit/test_config.py`
- `tests/unit/routers/__init__.py`
- `tests/unit/routers/conftest.py`
- `tests/unit/routers/test_health.py`
- `tests/unit/routers/test_agents.py`
- `tests/integration/conftest.py`
- `tests/integration/test_endpoints.py`
- `tests/performance/conftest.py`
- `tests/performance/test_performance.py`
