# Agent Task Economy - AI Agent Instructions

## Project Overview

Agent Task Economy is a Python microservices project that implements a micro-economy where autonomous agents earn, spend, and compete for work. The system incentivizes precise task specifications through market pressure and dispute mechanics, using LLM-as-a-Judge panels for dispute resolution. The core thesis: AI is moving toward specification-driven development, so specification quality should be a first-class economic signal.

The economy operates through five services: an Identity & PKI service for agent registration and Ed25519 signature verification, a Central Bank for ledger management and escrow, a Task Board for task lifecycle and bidding, a Reputation service for tracking specification and delivery quality, and a Civil Claims Court for LLM-based dispute resolution.

## Build & Run

```bash
just help             # Show all available commands
just init-all         # Initialize all service environments
just start-all        # Start all services in background
just stop-all         # Stop all locally running services
just status           # Check health status of all services
just test-all         # Run all tests
just ci-all           # Run all CI checks (verbose)
just ci-all-quiet     # Run all CI checks (quiet)
just destroy-all      # Remove all virtual environments
```

### Docker

```bash
just docker-up        # Start all services (Docker)
just docker-up-dev    # Start with hot reload
just docker-down      # Stop all services
just docker-logs [service]  # View logs
just docker-build     # Build all Docker images
```

### Per-Service Commands

Run from within `services/<name>/`:

```bash
just init             # Initialize service environment
just destroy          # Remove virtual environment
just run              # Run service locally with hot reload
just test             # Run all tests (unit + integration)
just test-unit        # Run unit tests only
just test-integration # Run integration tests only
just test-coverage    # Run tests with coverage report
just ci               # Run all CI checks (verbose)
just ci-quiet         # Run all CI checks (quiet)
just code-format      # Auto-fix formatting
just code-style       # Check style (read-only)
just code-typecheck   # Run mypy
just code-lspchecks   # Run pyright (strict)
just code-security    # Run bandit
just code-deptry      # Check dependencies
just code-spell       # Check spelling
just code-semgrep     # Run custom rules
just code-audit       # Vulnerability scan
```

## Testing

- After **every change** to the code, the tests must be executed
- Always verify the program runs correctly with `just run` after modifications
- Always run `just test-all` or `just ci-all-quiet` to verify changes before claiming they work
- **Tests are acceptance tests — do NOT modify existing test files.** Add new test files to cover new or additional requirements instead.
- Tests must be marked with `@pytest.mark.unit`, `@pytest.mark.integration`, or `@pytest.mark.performance`

## Architecture

```
services/
  identity/             Agent registration & Ed25519 signature verification (port 8001)
  central-bank/         Ledger, escrow, salary distribution (port 8002)
  task-board/           Task lifecycle, bidding, contracts, asset store (port 8003)
  reputation/           Spec quality & delivery quality scores, feedback (port 8004)
  court/                LLM-as-a-Judge dispute resolution (port 8005)
libs/
  service-commons/      Shared FastAPI infrastructure (config, logging, exceptions)
tools/                  Simulation injector & CLI utilities
tests/                  Cross-service integration tests
config/
  semgrep/              Static analysis rules
  codespell/            Spell-check ignore list
docs/
  plans/                Design documents and implementation plans (date-prefixed)
  specifications/
    service-api/        API specs per service
    service-tests/      Test specs per service
  codex-tasks/          Phased implementation task plans for agents
  diagrams/             System diagrams and sequence diagrams
  demo-scenarios/       Demo scenario descriptions
  explanations/         Technical explainers
  service-implementation-guide.md   How to implement a service from scaffolding
```

### Service Layout

Each service in `services/` follows this layout:

```
services/<service-dir>/
├── config.yaml                         # Service configuration (YAML)
├── justfile                            # Service-specific commands
├── pyproject.toml                      # Dependencies and tool config
├── pyrightconfig.json                  # Strict type checking config
├── Dockerfile                          # Container definition
├── src/<service_name>/
│   ├── __init__.py                     # Package marker + __version__
│   ├── app.py                          # FastAPI application factory (create_app)
│   ├── config.py                       # Pydantic settings (loads config.yaml)
│   ├── logging.py                      # Service-specific logging wrapper
│   ├── schemas.py                      # Pydantic request/response models
│   ├── routers/
│   │   ├── __init__.py
│   │   ├── health.py                   # GET /health endpoint
│   │   └── <domain>.py                 # Service-specific API endpoints
│   ├── core/
│   │   ├── __init__.py
│   │   ├── state.py                    # AppState dataclass + global singleton
│   │   ├── lifespan.py                 # FastAPI lifespan (startup/shutdown)
│   │   └── exceptions.py              # Exception handlers (wired to service-commons)
│   └── services/
│       ├── __init__.py
│       └── <domain>.py                 # Business logic layer (no FastAPI imports)
└── tests/
    ├── conftest.py
    ├── unit/                           # Fast, isolated, no external deps
    │   ├── conftest.py
    │   ├── test_config.py
    │   ├── test_<domain>.py
    │   └── routers/
    │       ├── conftest.py
    │       ├── test_health.py
    │       └── test_<domain>.py
    ├── integration/                    # Require running service
    │   ├── conftest.py
    │   └── test_endpoints.py
    └── performance/                    # Latency/throughput benchmarks
        ├── conftest.py
        └── test_performance.py
```

See `docs/service-implementation-guide.md` for detailed file-by-file implementation patterns.

### Key Design Principles

- Services communicate via HTTP/JSON using `httpx` for async clients
- All endpoints follow a uniform API structure
- Health checks: `GET /health` returns `{"status": "ok", "uptime_seconds": ..., "started_at": ...}`
- All services return errors in a consistent JSON format: `{"error": "ERROR_CODE", "message": "...", "details": {...}}`
- The `create_app()` factory pattern is used for all services (uvicorn `--factory` flag)
- Application state is managed via a global `AppState` singleton initialized during lifespan
- Business logic lives in `services/` — routers are thin wrappers that parse requests and call the services layer
- Agents prove identity by signing payloads with Ed25519 private keys; the Identity service verifies signatures against stored public keys
- Ambiguous task specifications are judged in favor of the worker (core incentive mechanism)

### Service Dependencies

```
Identity (port 8001) ← no dependencies (leaf service)
Central Bank (port 8002) ← Identity
Task Board (port 8003) ← Identity, Central Bank
Reputation (port 8004) ← Identity
Court (port 8005) ← Identity, Task Board, Reputation, Central Bank
```

### Task Lifecycle

```
1. POSTING      → Poster signs & publishes task (spec, reward, deadlines) → escrow locks funds
2. BIDDING      → Agents submit signed bids (binding, no withdrawal)
3. ACCEPTANCE   → Poster accepts a bid → platform co-signs contract → escrow locks funds
4. EXECUTION    → Agent works on task, clock is ticking (completion deadline)
5. SUBMISSION   → Agent uploads deliverables to platform asset store
6. REVIEW       → Poster has [configurable] window to review
   ├─ APPROVE   → Full payout to agent, mutual feedback exchange
   ├─ TIMEOUT   → Auto-approve, full payout to agent
   └─ DISPUTE   → Poster files claim → agent submits rebuttal → Court
7. RULING       → Judges evaluate → proportional payout → reputation scores updated
```

## Delegating Work

See [DELEGATE.md](DELEGATE.md) for instructions on delegating work to sub-agents via tmux.

## Git Rules

- **Never use `git -C <path>`** to operate on other worktrees. Always use the full `git` command from the current working directory.

## Code Style

### General

- **Never assume any default values anywhere** — always be explicit about values, paths, and configurations
- If a value is not provided, handle it explicitly (raise error, use null, or prompt for input)
- **Never create Python files in the project root**

### Python Execution

- Python code must be executed **only** via `uv run ...`
  - Example: `uv run uvicorn identity_service.app:create_app --factory --reload`
  - **Never** use: `python`, `python3`, or direct script execution
- Virtual environments are created via `uv sync`
- Each service has its **own** virtual environment in `services/<name>/.venv/`
- **Never** use: `pip install`, `python -m pip`, or `uv pip`
- All dependencies declared in service's `pyproject.toml`

### Configuration

- **Never hardcode configuration values** in application code
- **Never use default parameter values** for configurable settings
- **All config must come from config.yaml** or environment variables
- **Fail fast** — invalid configuration crashes at startup, not runtime
- **Type-safe access** — use `settings.section.key`, never `config["section"]["key"]`

```python
# WRONG - hardcoded default
def verify_signature(algorithm: str = "ed25519"):
    ...

# WRONG - buried default
db_url = config.get("database_url", "sqlite:///data/default.db")

# CORRECT - explicit from config
from identity_service.config import get_settings

settings = get_settings()

def verify_signature(algorithm: str):  # No default
    ...

verify_signature(algorithm=settings.crypto.algorithm)
```

### Error Handling

- Services should return consistent error responses using `ServiceError`
- HTTP status codes must be explicit and appropriate
- Failed operations should be logged with structured context
- Scripts should track and report success/failure counts
- Exit with code 1 if any items failed, 0 if all succeeded

### HTTP Status Codes

- `400` - Bad request (invalid input)
- `404` - Resource not found (e.g., agent_id not found)
- `409` - Conflict (e.g., public key already registered)
- `422` - Validation error (valid JSON, invalid content)
- `500` - Internal server error
- `503` - Service unavailable

## Files to Never Edit Directly

- `data/` - runtime data (gitignored)
- `reports/` - generated test artifacts
- `uv.lock` - regenerated by `uv sync`
- `libs/service-commons/` - shared library, changes affect all services

## Common Workflows

### Working on a single service

1. Navigate to service: `cd services/<name>`
2. Initialize environment: `just init`
3. Run locally with hot reload: `just run`
4. Run tests: `just test`
5. Run all CI checks: `just ci`

### Adding a dependency

1. Edit `pyproject.toml` in the service directory to add the dependency
2. Run `uv sync --all-extras` from the service directory

### Implementing a new service from scaffolding

1. Read `docs/service-implementation-guide.md` for the complete file-by-file guide
2. Read the service's API specification in `docs/specifications/`
3. Follow the implementation sequence in the guide
4. Verify with `just run`, then `just test`, then `just ci`

### Fixing a bug

1. Write a failing test first
2. Verify it fails with `just test-all`
3. Implement the fix
4. Verify it passes with `just test-all`

## Ticket Management

Every feature request or bug fix must have a corresponding test ticket that blocks it. The test ticket describes how to write a failing test that confirms the feature is not yet implemented or the bug still exists. The implementation ticket depends on the test ticket — no implementation work begins until the failing test is written and verified.

### Workflow

1. Create a test ticket: "Write acceptance tests for: \<feature/bug summary\>"
2. Create the implementation ticket: "\<feature/bug summary\>"
3. Add a dependency: implementation ticket depends on test ticket
4. Write the failing test first, verify it fails
5. Close the test ticket
6. Implement the feature/fix, verify the test passes
7. Close the implementation ticket

### Rules

- **No implementation without a failing test** — every implementation ticket must be blocked by a test ticket
- **Tests must fail first** — a test ticket is only closed once the test exists and fails against current code
- **Test describes the "what", not the "how"** — test tickets describe observable behavior to assert, not implementation details

## Service Development Workflow

New services are built through a strict sequence of phases, each producing a concrete artifact before the next begins. Tests are always written and validated before any implementation starts.

### Phase 1: Write the Specification

Create two documents:

1. **API Specification** — defines endpoints, data models, request/response formats, error codes, and interaction patterns. Lives in `docs/specifications/service-api/`.
2. **Test Specification** — defines every acceptance test case (status codes, error codes, assertions) that the service must pass before release. Lives in `docs/specifications/service-tests/`.

The test spec is the release gate. If a behavior is not in the test spec, it is out of scope. If it is in the test spec, it must pass.

**Identity Service reference:**
- API spec: `docs/specifications/service-api/identity-service-specs.md`
- Test spec: `docs/specifications/service-tests/identity-service-tests.md`

### Phase 2: Write the Implementation Task Plan

Create a phased implementation plan that an agent can execute mechanically. The plan breaks the work into ordered phases, lists every file to create or modify, and specifies verification commands after each phase. Lives in `docs/codex-tasks/`.

The plan references:
- The API specification (source of truth for behavior)
- The test specification (source of truth for pass/fail)
- `docs/service-implementation-guide.md` (file-by-file patterns)
- `AGENTS.md` (project conventions)

**Identity Service reference:**
- Implementation plan: `docs/codex-tasks/identity-service-implementation/README.md` (with `phase-1-config.md` through `phase-8-verification.md`)
- Test implementation plan: `docs/codex-tasks/identity-service-tests.md`

### Phase 3: Implement Tests (Separate Session, Worktree)

Start a new git worktree for isolation. Delegate to a sub-agent (via `DELEGATE.md`) to implement **all tests before any features**.

1. Create a worktree: `git worktree add .claude/worktrees/<service>-tests -b <service>-tests`
2. Prime the agent with these documents (in order):
   - `AGENTS.md` — project conventions
   - The test specification from `docs/specifications/service-tests/`
   - The API specification from `docs/specifications/service-api/`
   - The test implementation plan from `docs/codex-tasks/`
3. The agent implements all test files — unit tests, integration tests, acceptance tests
4. **No implementation code is written in this phase** — only tests
5. Run `just ci-quiet` from the service directory to validate that all test files comply with coding rules (formatting, linting, type checking, spelling, security)
6. Tests are expected to fail (the service is not implemented yet) — but they must be **syntactically valid and CI-compliant**

### Phase 4: Implement the Service (Separate Session, Same or New Worktree)

Start a new session. Delegate to a sub-agent to implement the service features.

1. Prime the agent with these documents (in order):
   - `AGENTS.md` — project conventions
   - The API specification from `docs/specifications/service-api/`
   - The test specification from `docs/specifications/service-tests/`
   - The implementation plan from `docs/codex-tasks/`
   - `docs/service-implementation-guide.md` — file-by-file patterns
2. The agent implements the service following the phased plan
3. After each phase, run verification commands specified in the plan
4. After all phases, run `just ci-quiet` from the service directory — this is the **definitive validation**
5. `just ci-quiet` runs the full CI pipeline: formatting, linting, type checking (mypy + pyright), security scanning, spell checking, custom semgrep rules, **and all tests**
6. The service is complete only when `just ci-quiet` passes with zero failures

### Key Principles

- **Tests before implementation** — always. No exceptions.
- **`just ci-quiet` is the only validation that matters** — not `just test` alone. CI catches formatting, lint, type errors, security issues, and spelling that `just test` misses.
- **Specifications are the source of truth** — the agent implements what the spec says, not what seems reasonable.
- **Worktrees for isolation** — each phase of work happens in an isolated worktree so `main` stays clean.
- **Separate sessions for tests vs. implementation** — this enforces the discipline that tests exist and are CI-compliant before any implementation begins.

## Landing the Plane (Session Completion)

**When ending a work session**, you MUST complete ALL steps below. Work is NOT complete until `git push` succeeds.

**MANDATORY WORKFLOW:**

1. **File issues for remaining work** - Create issues for anything that needs follow-up
2. **Run quality gates** (if code changed) - Tests, linters, builds
3. **Update issue status** - Close finished work, update in-progress items
4. **PUSH TO REMOTE** - This is MANDATORY:
   ```bash
   git pull --rebase
   bd sync
   git push
   git status  # MUST show "up to date with origin"
   ```
5. **Clean up** - Clear stashes, prune remote branches
6. **Verify** - All changes committed AND pushed
7. **Hand off** - Provide context for next session

**CRITICAL RULES:**
- Work is NOT complete until `git push` succeeds
- NEVER stop before pushing - that leaves work stranded locally
- NEVER say "ready to push when you are" - YOU must push
- If push fails, resolve and retry until it succeeds
