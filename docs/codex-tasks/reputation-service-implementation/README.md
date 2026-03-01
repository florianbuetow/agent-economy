# Reputation Service — Implementation Plan

The Reputation service (port 8004) is the quality signal of the Agent Task Economy. It records bidirectional feedback between task posters and workers, providing raw data that drives agent specialization and market self-correction. Feedback is sealed on submission and revealed only when both parties have rated each other (or a configurable timeout expires). The service uses SQLite for persistence and delegates JWS signature verification to the Identity service.

The Reputation service depends on Identity (JWS verification). It does not depend on Central Bank, Task Board, or Court at this time.

---

## Files to Read First

The implementing agent MUST read these documents in order before writing any code:

1. `AGENTS.md` — project conventions, code style, testing rules
2. `docs/specifications/service-api/reputation-service-specs.md` — API specification (source of truth for behavior)
3. `docs/specifications/service-api/reputation-service-auth-specs.md` — authentication specification
4. `docs/specifications/service-tests/reputation-service-tests.md` — test specification (source of truth for pass/fail)
5. `docs/specifications/service-tests/reputation-service-auth-tests.md` — auth test specification
6. `docs/service-implementation-guide.md` — file-by-file implementation patterns

Additionally, reference these existing implementations for established patterns:

- `services/central-bank/` — IdentityClient, middleware, JWS validation in routers, SQLite store patterns
- `services/identity/` — config.py structure, health endpoint, exception handlers

---

## Global Rules

- All Python execution via `uv run` — never raw `python`, `python3`, or `pip install`
- No default parameter values for configurable settings — all config from `config.yaml`
- `ConfigDict(extra="forbid")` on every Pydantic model
- Business logic in `services/` — routers are thin wrappers
- No FastAPI imports in `services/` layer
- Tests marked with `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.performance`
- Do NOT modify existing test files — add new files only
- Working directory for all phases: `services/reputation/`

---

## Implementation Phases

| Phase | File | Description |
|-------|------|-------------|
| 1 | `phase-1-config.md` | Dependencies (`pyproject.toml`) and configuration (`config.yaml`) |
| 2 | `phase-2-foundation.md` | `__init__.py`, `config.py`, `logging.py`, `schemas.py` |
| 3 | `phase-3-core.md` | `core/state.py`, `core/exceptions.py`, `core/middleware.py`, `core/__init__.py`, `core/lifespan.py` |
| 4 | `phase-4-clients.md` | `services/identity_client.py`, `services/feedback_store.py` |
| 5 | `phase-5-service.md` | `services/feedback.py`, `services/__init__.py` |
| 6 | `phase-6-routers.md` | `routers/health.py`, `routers/feedback.py`, `routers/__init__.py` |
| 7 | `phase-7-app.md` | `app.py` — application assembly |
| 8 | `phase-8-tests.md` | All test files (unit, integration, performance stubs) |
| 9 | `phase-9-verification.md` | Full verification and troubleshooting |

---

## Verification After Each Phase

After **every phase**, run from `services/reputation/`:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

After **Phase 7** (app assembly), also run:

```bash
just run
# In another terminal:
curl -s http://localhost:8004/health | python3 -m json.tool
just stop  # or Ctrl-C
```

After **Phase 8** (tests), run:

```bash
just ci-quiet
```

After **Phase 9**, `just ci-quiet` must pass with zero failures. That is the only gate that matters.

---

## Complete File Inventory

### Modify Existing Files

| File | Change |
|------|--------|
| `pyproject.toml` | Add runtime dependency: `httpx` |
| `config.yaml` | Replace with full reputation-specific configuration |
| `src/reputation_service/__init__.py` | Add `__version__` and docstring |

### Create New Source Files

| File | Purpose |
|------|---------|
| `src/reputation_service/config.py` | Pydantic settings (loads config.yaml) |
| `src/reputation_service/logging.py` | Service-specific logging wrapper |
| `src/reputation_service/schemas.py` | All request/response Pydantic models |
| `src/reputation_service/app.py` | FastAPI application factory |
| `src/reputation_service/core/__init__.py` | Core package exports |
| `src/reputation_service/core/state.py` | AppState dataclass + FeedbackRecord + global singleton |
| `src/reputation_service/core/exceptions.py` | Exception handlers |
| `src/reputation_service/core/middleware.py` | Body size + content-type ASGI middleware |
| `src/reputation_service/core/lifespan.py` | Startup/shutdown lifecycle |
| `src/reputation_service/routers/__init__.py` | Router package exports |
| `src/reputation_service/routers/health.py` | GET /health |
| `src/reputation_service/routers/feedback.py` | POST /feedback, GET /feedback/{id}, GET /feedback/task/{id}, GET /feedback/agent/{id} |
| `src/reputation_service/services/__init__.py` | Service layer exports |
| `src/reputation_service/services/identity_client.py` | JWS verification via Identity service |
| `src/reputation_service/services/feedback_store.py` | SQLite-backed feedback persistence with atomic mutual reveal |
| `src/reputation_service/services/feedback.py` | Feedback validation + business logic (pure Python, no FastAPI) |

### Create New Test Files

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Shared test config (minimal) |
| `tests/helpers.py` | JWS token generation, mock IdentityClient factory |
| `tests/unit/conftest.py` | Auto-clear settings cache + app state |
| `tests/unit/test_config.py` | Config loading tests |
| `tests/unit/test_feedback_service.py` | Feedback validation + business logic tests |
| `tests/unit/test_persistence.py` | SQLite FeedbackStore tests (mutual reveal, restart, integrity) |
| `tests/unit/test_store_robustness.py` | DuplicateFeedbackError, ROLLBACK safety, thread safety |
| `tests/unit/routers/conftest.py` | Router fixtures: app, client, JWS helpers, mocks |
| `tests/unit/routers/test_health.py` | Health endpoint tests |
| `tests/unit/routers/test_feedback.py` | Feedback endpoint tests (core submission + retrieval) |
| `tests/unit/routers/test_feedback_auth.py` | JWS auth tests (AUTH-01 to AUTH-16, PREC-01 to PREC-07, VJWS-01 to VJWS-09) |
| `tests/unit/routers/test_identity_error_remapping.py` | IdentityClient error classification tests |
| `tests/integration/conftest.py` | Integration test config |
| `tests/integration/test_endpoints.py` | Multi-endpoint workflow tests |
| `tests/architecture/test_architecture.py` | Import rule tests (pytestarch) |
| `tests/performance/conftest.py` | Performance test config (stub) |
