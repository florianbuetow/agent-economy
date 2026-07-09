# Full-CI Reliability Fix Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make root `just ci-quiet` and `just test-all` reliably pass on a clean machine by removing the `start-all` startup race and making `test-all` provide the live stack its integration tests require.

**Architecture:** Two independent justfile changes. (1) In `start-all`, boot the Identity service first — it is the **register** (registry) that every other service registers its agent against — and **block until it is fully online (registration-ready)** before launching any service that depends on it, turning a timing race into a deterministic ordering. (2) Wrap root `test-all` in `start-all`/`stop-all` (mirroring `test-e2e`) so db-gateway's HTTP-based integration test has a live service to hit.

**Tech Stack:** `just` recipes (bash), uvicorn, FastAPI lifespans, httpx, pytest.

## Global Constraints

- **Do NOT modify existing test files** — they are acceptance tests. Add new test files for new coverage. (This is why the fixes below are justfile-only.)
- Python executed **only** via `uv run ...`; never `python`, `pip`, or `uv pip`.
- The validation gate is root **`just ci-quiet`** (services + agents + cross-service integration + e2e), run from the repo root.
- Never use `git -C <path>` on other worktrees; run `git` from the cwd.
- Only touch the two files named in this plan (`justfile`). No service code changes are required by the primary fix.

---

## Startup Ordering Requirement (must-have, acceptance criterion for Task 1)

`start-all` **must** boot the Identity service — the **register** that every
worker and platform agent registers against — **first**, and **block until it is
fully online** before starting any other service. Only after that gate passes
may central-bank, task-board, reputation, court, and ui launch.

**"Fully online" is defined concretely** so it can't be misread as "process
spawned":
1. `wait_for_health "Identity" 8001` returns success (`/health` reports `"ok"`).
   uvicorn serves `/health` **only after** Identity's lifespan finishes
   `init_app_state()` (`services/identity/src/identity_service/core/lifespan.py:31`)
   and mounts its routes — so a healthy Identity is already registration-ready
   (`POST /agents/register`, `routers/agents.py:74`).
2. **AND** the registry actually serves a read: `GET http://localhost:8001/agents`
   (`routers/agents.py:185`) returns a body containing `"agents"`. This proves
   the registry capability the dependents need is live, not merely that the port
   is bound.

If either check fails, `start-all` aborts (exit 1) instead of launching
dependents that would crash with `ConnectError`. Task 1 implements this gate;
this section is its acceptance criterion.

## Background — Root Cause (evidence)

### Issue 1 — `start-all` tier-2 startup race (the real flake; fails `ci-quiet` Phase 4 e2e)

`justfile` `start-all` starts services in tiers. Tier 2 launches Identity (8001) **and its dependents in parallel**:

```
justfile (current, ~lines 249-264)
  cd services/identity   && uvicorn ... 8001 &
  cd services/reputation && uvicorn ... 8004 &     # registers against Identity on startup
  cd services/central-bank && uvicorn ... 8002 &   # registers against Identity on startup
  cd services/task-board && uvicorn ... 8003 &     # registers against Identity on startup
  cd services/court      && uvicorn ... 8005 &     # registers against Identity on startup
  cd services/ui         && uvicorn ... 8008 &     # registers, but degrades gracefully on failure
  wait_for_health "Identity" 8001                  # <-- the wait happens AFTER all are launched
  ...
```

Four service lifespans call `await platform_agent.register()` during startup, which POSTs to Identity (`agents/src/base_agent/mixins/identity.py:32-67`, no connection retry):

- `services/central-bank/src/central_bank_service/core/lifespan.py:59`
- `services/court/src/court_service/core/lifespan.py:109`
- `services/reputation/src/reputation_service/core/lifespan.py:55`
- `services/task-board/src/task_board_service/core/lifespan.py:75`

If a dependent's lifespan reaches `register()` before Identity's uvicorn has bound its socket, the POST raises `httpx.ConnectError: All connection attempts failed`, the lifespan aborts, and uvicorn logs `Application startup failed. Exiting.` Observed in a real CI run: **Reputation** lost the race and exited; `wait_for_health "Reputation" 8004` then reported `✗ Reputation failed to start`, and every e2e test failed with connection errors. A clean retry passed (timing-dependent) — proving it is a race, not a code defect. `UI` registers a user-agent too (`services/ui/src/ui_service/core/lifespan.py:53`) but catches the failure and continues in a degraded "proxy endpoints unavailable" mode, so it does not hard-fail.

### Issue 2 — `just test-all` requires a live stack (fails standalone)

Root `just test-all` runs each service's `just test` (unit + architecture + integration). Most services' integration tests run **in-process** via `httpx.ASGITransport` (e.g. `services/ui/tests/integration/conftest.py:88-93`) and need no live service. But **db-gateway**'s `services/db-gateway/tests/integration/test_endpoints.py::TestHealthIntegration::test_health_check` issues a real HTTP `GET http://localhost:8007/health`. `test-all` does not start services, so on a clean machine that test fails with `httpx.ConnectError: [Errno 61] Connection refused`, aborting `test-all`. (These live tests are not part of `ci-quiet` — per-service `ci-quiet` runs unit + architecture only — which is why the gate is green while `test-all` is red.)

---

## File Structure

| File | Responsibility | Change |
|------|----------------|--------|
| `justfile` (recipe `start-all`, ~lines 244-269) | Service boot orchestration | Split tier 2: Identity first + health-gate, then the rest |
| `justfile` (recipe `test-all`, ~lines 680-691) | Run every service's full test suite | Wrap in `start-all`/`stop-all` with a teardown trap |

No application/service/test code changes. Both fixes are localized to `justfile`.

---

## Task 1: Make `start-all` start Identity before its registrants

**Files:**
- Modify: `justfile` — recipe `start-all`, the "Tier 2" block (~lines 249-264)

**Interfaces:**
- Consumes: the existing `wait_for_health <name> <port>` shell function already defined in `start-all` (`justfile:227-242`); returns 0 when `/health` reports `"ok"`, 1 after 60 attempts.
- Produces: a `start-all` that guarantees Identity (8001) is **fully online** — `/health` ok **and** `GET /agents` serving — before reputation/central-bank/task-board/court/ui launch (satisfies the Startup Ordering Requirement above).

- [ ] **Step 1: Reproduce the race (best-effort red).** A timing race has no deterministic failing test; expose it by repetition. From repo root:

```bash
for i in 1 2 3; do just stop-all >/dev/null 2>&1; just start-all 2>&1 | grep -E "failed to start|Application startup failed" && echo "RACE on run $i"; just stop-all >/dev/null 2>&1; done
echo "done"
```

Expected (intermittent): at least one run prints `✗ <Service> failed to start` / `RACE on run i`. If three runs are clean it may not have triggered — proceed; Step 4 verifies the fix holds across repeated runs regardless.

- [ ] **Step 2: Apply the ordering fix.** Replace the current Tier 2 block:

```bash
    # Tier 2: All remaining services in parallel (DB Gateway is ready)
    printf "Starting tier 2 (all remaining services)...\n"
    cd services/identity && uv run uvicorn identity_service.app:create_app --factory --host 127.0.0.1 --port 8001 &
    cd services/reputation && uv run uvicorn reputation_service.app:create_app --factory --host 127.0.0.1 --port 8004 &
    cd services/central-bank && uv run uvicorn central_bank_service.app:create_app --factory --host 127.0.0.1 --port 8002 &
    cd services/task-board && uv run uvicorn task_board_service.app:create_app --factory --host 127.0.0.1 --port 8003 &
    cd services/court && set -a && [ -f .env ] && . .env && set +a && uv run uvicorn court_service.app:create_app --factory --host 127.0.0.1 --port 8005 &
    cd services/ui && uv run uvicorn ui_service.app:create_app --factory --host 127.0.0.1 --port 8008 &

    # Wait in dependency order
    wait_for_health "Identity" 8001
    wait_for_health "Central Bank" 8002
    wait_for_health "Task Board" 8003
    wait_for_health "Reputation" 8004
    wait_for_health "Court" 8005
    wait_for_health "UI" 8008
```

with this (Identity first + health gate, then the rest):

```bash
    # Tier 2: Identity first. It is the leaf service that central-bank,
    # task-board, reputation, and court each register their platform agent
    # against during startup. Launching it concurrently with those dependents
    # caused a race: a dependent could POST /agents/register before Identity
    # had bound its socket, raising httpx.ConnectError and aborting that
    # service's lifespan ("Application startup failed. Exiting.").
    printf "Starting tier 2 (Identity)...\n"
    cd services/identity && uv run uvicorn identity_service.app:create_app --factory --host 127.0.0.1 --port 8001 &
    if ! wait_for_health "Identity" 8001; then
        printf "\033[0;31m✗ Identity did not become healthy; aborting startup\033[0m\n"
        exit 1
    fi
    # Fully-online gate: confirm the registry actually serves reads, not just
    # that the port is bound. Dependents will POST /agents/register, so assert
    # GET /agents answers before launching them.
    if ! curl -s --connect-timeout 1 "http://localhost:8001/agents" | grep -q '"agents"'; then
        printf "\033[0;31m✗ Identity health OK but /agents not serving; aborting startup\033[0m\n"
        exit 1
    fi
    printf "\033[0;32m✓ Identity registry fully online (port 8001)\033[0m\n"

    # Tier 3: remaining services in parallel. Identity is healthy, so
    # platform-agent registration can no longer race.
    printf "Starting tier 3 (remaining services)...\n"
    cd services/reputation && uv run uvicorn reputation_service.app:create_app --factory --host 127.0.0.1 --port 8004 &
    cd services/central-bank && uv run uvicorn central_bank_service.app:create_app --factory --host 127.0.0.1 --port 8002 &
    cd services/task-board && uv run uvicorn task_board_service.app:create_app --factory --host 127.0.0.1 --port 8003 &
    cd services/court && set -a && [ -f .env ] && . .env && set +a && uv run uvicorn court_service.app:create_app --factory --host 127.0.0.1 --port 8005 &
    cd services/ui && uv run uvicorn ui_service.app:create_app --factory --host 127.0.0.1 --port 8008 &

    # Wait for the rest in dependency order
    wait_for_health "Central Bank" 8002
    wait_for_health "Task Board" 8003
    wait_for_health "Reputation" 8004
    wait_for_health "Court" 8005
    wait_for_health "UI" 8008
```

Notes for the implementer:
- Each `cd services/X && ... &` runs in its own backgrounded subshell, so the `cd`s do not affect the parent shell's cwd — preserve that pattern exactly.
- The `start-all` recipe shebang has no `set -e`; the explicit `if ! wait_for_health ...; then exit 1` is required to fail fast when Identity genuinely cannot start (otherwise the dependents would all fail downstream with confusing errors).
- Do not change the Tier 1 (DB Gateway) block above it or the success banner below it.

- [ ] **Step 3: Lint the justfile.** Run:

```bash
just --evaluate >/dev/null && echo "justfile parses OK"
```

Expected: `justfile parses OK` (no parse error).

- [ ] **Step 4: Verify the race is gone (green).** Run e2e three times in a row; each run does a clean `stop-all` → wipe → `start-all` → tests:

```bash
for i in 1 2 3; do echo "=== e2e run $i ==="; just test-e2e 2>&1 | grep -E "Identity registry fully online|All services started|failed to start|Application startup failed|passed|failed"; done
```

Expected each run: `✓ Identity registry fully online (port 8001)` appears **before** `✓ All services started`, followed by a pytest summary line ending in `passed` (e.g. `58 passed`), with **no** `failed to start` / `Application startup failed` lines.

- [ ] **Step 5: Commit.**

```bash
git add justfile
git commit -m "fix(ci): start Identity before its registrants in start-all to kill startup race"
```

---

## Task 2: Make `just test-all` provide the live stack its integration tests need

**Depends on Task 1** (so `test-all`'s `start-all` does not itself race).

**Files:**
- Modify: `justfile` — recipe `test-all` (~lines 680-691)

**Interfaces:**
- Consumes: `just start-all`, `just stop-all`, per-service `just test`.
- Produces: a `test-all` that, run from a clean machine with no services up, brings the stack up, runs all per-service suites, and tears the stack down even on failure.

- [ ] **Step 1: Reproduce the failure (red).** From repo root with nothing running:

```bash
just stop-all >/dev/null 2>&1; just test-all 2>&1 | tail -5
```

Expected: ends with a failure in `services/db-gateway` —
`FAILED tests/integration/test_endpoints.py::TestHealthIntegration::test_health_check - httpx.ConnectError: [Errno 61] Connection refused` and `error: Recipe \`test-all\` failed`.

- [ ] **Step 2: Apply the fix.** Replace the current recipe:

```bash
test-all:
    @echo ""
    @printf "\033[0;34m=== Running All Tests ===\033[0m\n"
    cd services/identity && just test
    cd services/central-bank && just test
    cd services/task-board && just test
    cd services/reputation && just test
    cd services/court && just test
    cd services/db-gateway && just test
    cd services/ui && just test
    @printf "\033[0;32m✓ All tests passed\033[0m\n"
    @echo ""
```

with a self-contained version that boots the stack (mirroring how `test-e2e` already manages services):

```bash
test-all:
    #!/usr/bin/env bash
    set -uo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running All Tests ===\033[0m\n"
    printf "\n"

    # Some per-service integration tests (db-gateway) hit a live service over
    # HTTP, so bring the full stack up first and guarantee teardown.
    cleanup() {
        printf "\033[0;34m--- Stopping all services ---\033[0m\n"
        cd "$root" && just stop-all
    }
    trap cleanup EXIT
    cd "$root" && just start-all

    fail=0
    for svc in identity central-bank task-board reputation court db-gateway ui; do
        printf "\033[0;34m--- %s ---\033[0m\n" "$svc"
        cd "$root/services/$svc" && just test || fail=1
        cd "$root"
    done

    printf "\n"
    if [ "$fail" -ne 0 ]; then
        printf "\033[0;31m✗ Some service test suites failed\033[0m\n"
        exit 1
    fi
    printf "\033[0;32m✓ All tests passed\033[0m\n"
    printf "\n"
```

Notes for the implementer:
- Unlike the original, this runs **all** services even if one fails (collecting `fail=1`) so you see every failure in one pass; it still exits non-zero if any failed.
- The `trap cleanup EXIT` guarantees `stop-all` runs even when a suite fails or the run is interrupted — do not remove it.

- [ ] **Step 3: Verify (green).** From a clean machine:

```bash
just stop-all >/dev/null 2>&1; just test-all 2>&1 | tail -6
```

Expected: db-gateway's `test_health_check` now passes (`GET /health HTTP/1.1" 200 OK` → `1 passed`), the run ends with `✓ All tests passed`, and the `--- Stopping all services ---` teardown appears. Confirm no listeners remain:

```bash
lsof -nP -iTCP:8001-8008 -sTCP:LISTEN || echo "clean"
```

Expected: `clean`.

- [ ] **Step 4: Commit.**

```bash
git add justfile
git commit -m "fix(ci): boot stack in test-all so live-service integration tests pass standalone"
```

---

## Alternatives Considered (deferred — not part of this plan)

- **Bounded retry in `register()`** (`agents/src/base_agent/mixins/identity.py`) or in `_request_raw` (`agents/src/base_agent/agent.py`): wrap the registration POST in a short connect-retry loop (e.g. 5×, 0.3s backoff) so registration survives a transient `ConnectError` regardless of boot order. More robust (also helps Docker/other orchestrators and any future ordering change), but touches shared agent code and is unnecessary once Task 1 makes the local ordering deterministic. Recommend as a follow-up hardening ticket, not a blocker.
- **Make db-gateway's integration tests in-process** (use `ASGITransport` like `services/ui/tests/integration/conftest.py`) or **skip-when-unreachable**: would let `test-all` pass without booting the stack. Rejected here because it modifies an existing acceptance test file (against the project's "do NOT modify existing test files" rule); Task 2's justfile wrap achieves the same green without touching tests. Revisit only if booting the stack for `test-all` proves too slow.

## Self-Review

1. **Spec coverage:** Issue 1 (startup race) → Task 1, whose gate satisfies the explicit Startup Ordering Requirement (boot the register first, block until fully online). Issue 2 (`test-all` needs live stack) → Task 2. Both issues from the investigation are covered.
2. **Placeholder scan:** No TBD/TODO; every change shows the exact before/after block and exact verification commands with expected output. (TDD-by-pytest is intentionally not used: Issue 1 is a timing race with no deterministic unit test — verified by repeated e2e runs; Issue 2 has a real red→green via `just test-all`.)
3. **Consistency:** `wait_for_health` name/signature matches its definition (`justfile:227`); service names/ports match the existing recipe; `start-all`/`stop-all`/`just test` references match existing recipes. Task 2 explicitly depends on Task 1.
