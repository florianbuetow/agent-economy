# Court Service — Implementation Plan

The Court service (port 8005) is the dispute resolution engine for the agent economy. When a poster rejects a deliverable, the platform files a dispute through the Court. An LLM judge panel evaluates the claim and rebuttal, issues a proportional ruling, and the Court orchestrates all post-ruling side effects: escrow split, reputation feedback, and task status update.

The Court depends on all four other services: Identity (JWS verification), Task Board (task data + ruling notification), Central Bank (escrow split), and Reputation (feedback recording).

---

## Files to Read First

The implementing agent MUST read these documents in order before writing any code:

1. `AGENTS.md` — project conventions, code style, testing rules
2. `docs/specifications/service-api/court-service-specs.md` — API specification (source of truth for behavior)
3. `docs/specifications/service-api/court-service-auth-specs.md` — authentication specification
4. `docs/specifications/service-tests/court-service-tests.md` — test specification (source of truth for pass/fail)
5. `docs/specifications/service-tests/court-service-auth-tests.md` — auth test specification
6. `docs/service-implementation-guide.md` — file-by-file implementation patterns

Additionally, reference these existing implementations for established patterns:

- `services/central-bank/` — IdentityClient, middleware, escrow split API, JWS validation in routers
- `services/reputation/` — feedback submission API, FeedbackStore SQLite patterns

---

## Global Rules

- All Python execution via `uv run` — never raw `python`, `python3`, or `pip install`
- No default parameter values for configurable settings — all config from `config.yaml`
- `ConfigDict(extra="forbid")` on every Pydantic model
- Business logic in `services/` — routers are thin wrappers
- No FastAPI imports in `services/` layer
- Tests marked with `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.performance`
- Do NOT modify existing test files — add new files only
- Working directory for all phases: `services/court/`

---

## Implementation Phases

| Phase | File | Description |
|-------|------|-------------|
| 1 | `phase-1-config.md` | Dependencies (`pyproject.toml`) and configuration (`config.yaml`) |
| 2 | `phase-2-foundation.md` | `__init__.py`, `config.py`, `logging.py`, `schemas.py` |
| 3 | `phase-3-core.md` | `core/state.py`, `core/exceptions.py`, `core/middleware.py`, `core/__init__.py`, `core/lifespan.py` |
| 4 | `phase-4-clients.md` | `services/identity_client.py`, `services/platform_signer.py`, `services/task_board_client.py`, `services/central_bank_client.py`, `services/reputation_client.py` |
| 5 | `phase-5-judges.md` | `judges/__init__.py`, `judges/base.py`, `judges/prompts.py`, `judges/llm_judge.py` |
| 6 | `phase-6-service.md` | `services/dispute_service.py`, `services/__init__.py` |
| 7 | `phase-7-routers.md` | `routers/health.py`, `routers/disputes.py`, `routers/__init__.py` |
| 8 | `phase-8-app.md` | `app.py` — application assembly |
| 9 | `phase-9-tests.md` | All test files (unit, integration, performance stubs) |
| 10 | `phase-10-verification.md` | Full verification and troubleshooting |

---

## Verification After Each Phase

After **every phase**, run from `services/court/`:

```bash
uv run ruff check src/ tests/
uv run ruff format --check src/ tests/
```

After **Phase 8** (app assembly), also run:

```bash
just run
# In another terminal:
curl -s http://localhost:8005/health | python3 -m json.tool
just stop  # or Ctrl-C
```

After **Phase 9** (tests), run:

```bash
just ci-quiet
```

After **Phase 10**, `just ci-quiet` must pass with zero failures. That is the only gate that matters.

---

## Complete File Inventory

### Modify Existing Files

| File | Change |
|------|--------|
| `pyproject.toml` | Add runtime dependencies: `httpx`, `cryptography`, `joserfc`, `litellm`, `pyyaml` |
| `config.yaml` | Replace with full court-specific configuration |
| `src/court_service/__init__.py` | Add `__version__` and docstring |

### Create New Source Files

| File | Purpose |
|------|---------|
| `src/court_service/config.py` | Pydantic settings (loads config.yaml) |
| `src/court_service/logging.py` | Service-specific logging wrapper |
| `src/court_service/schemas.py` | All request/response Pydantic models |
| `src/court_service/app.py` | FastAPI application factory |
| `src/court_service/core/__init__.py` | Core package exports |
| `src/court_service/core/state.py` | AppState dataclass + global singleton |
| `src/court_service/core/exceptions.py` | Exception handlers |
| `src/court_service/core/middleware.py` | Body size + content-type ASGI middleware |
| `src/court_service/core/lifespan.py` | Startup/shutdown lifecycle |
| `src/court_service/routers/__init__.py` | Router package exports |
| `src/court_service/routers/health.py` | GET /health |
| `src/court_service/routers/disputes.py` | All dispute endpoints |
| `src/court_service/services/__init__.py` | Service layer exports |
| `src/court_service/services/identity_client.py` | JWS verification via Identity service |
| `src/court_service/services/platform_signer.py` | Ed25519 signing for outgoing requests |
| `src/court_service/services/task_board_client.py` | Task Board HTTP client |
| `src/court_service/services/central_bank_client.py` | Central Bank HTTP client |
| `src/court_service/services/reputation_client.py` | Reputation HTTP client |
| `src/court_service/services/dispute_service.py` | Dispute store + business logic |
| `src/court_service/judges/__init__.py` | Judge package exports |
| `src/court_service/judges/base.py` | JudgeVote dataclass + Judge ABC |
| `src/court_service/judges/prompts.py` | System prompt + evaluation template |
| `src/court_service/judges/llm_judge.py` | LiteLLM-based judge implementation |

### Create New Test Files

| File | Purpose |
|------|---------|
| `tests/conftest.py` | Shared test config (minimal) |
| `tests/unit/conftest.py` | Auto-clear settings cache + app state |
| `tests/unit/test_config.py` | Config loading tests |
| `tests/unit/test_dispute_service.py` | Dispute service unit tests |
| `tests/unit/test_judges.py` | Judge system unit tests |
| `tests/unit/routers/conftest.py` | Router fixtures: app, client, JWS helpers, mocks |
| `tests/unit/routers/test_health.py` | Health endpoint tests |
| `tests/unit/routers/test_disputes.py` | Dispute endpoint tests |
| `tests/integration/conftest.py` | Integration test config (stub) |
| `tests/performance/conftest.py` | Performance test config (stub) |
