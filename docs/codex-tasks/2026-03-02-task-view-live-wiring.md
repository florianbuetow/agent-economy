# Task View Live Wiring — Codex Execution Plan

## Overview

Replace the scripted task lifecycle demo with a live, data-driven view. This involves backend changes (UserAgent class, proxy endpoints) and frontend changes (task.js rewrite, task.html cleanup, shared.js cleanup).

## Pre-Flight

Read these files FIRST before doing anything:
1. `AGENTS.md` — project conventions
2. `docs/plans/2026-03-02-task-view-live-wiring-plan.md` — the full implementation plan with all code
3. `docs/plans/2026-03-02-task-view-live-wiring-design.md` — the design document

## Rules

- Use `uv run` for all Python execution — never raw python, python3, or pip install
- Do NOT modify any existing test files
- All config must come from config.yaml, never hardcoded
- Pydantic models use `ConfigDict(extra="forbid")`
- Commit after each phase completes

---

## Phase 1: Create UserAgent Class

### Files to create/modify:
- Create: `agents/src/base_agent/user_agent.py`
- Modify: `agents/src/base_agent/__init__.py`
- Modify: `agents/src/base_agent/factory.py`

### Steps:

1. Create `agents/src/base_agent/user_agent.py` with this exact content:

```python
"""UserAgent — UI-driven agent for human task lifecycle operations."""

from __future__ import annotations

from base_agent.platform import PlatformAgent


class UserAgent(PlatformAgent):
    """Agent used by the UI service for human-driven task lifecycle operations.

    Inherits PlatformAgent's keys and identity. All operations appear as
    the platform agent. Provides the same task-board, bank, and reputation
    methods as any other agent.
    """

    def __repr__(self) -> str:
        registered = f", agent_id={self.agent_id!r}" if self.agent_id else ""
        return f"UserAgent(name={self.name!r}{registered})"
```

2. Edit `agents/src/base_agent/__init__.py` — add the UserAgent import and export:

```python
"""Base Agent — programmable client for the Agent Task Economy platform."""

from base_agent.agent import BaseAgent
from base_agent.factory import AgentFactory
from base_agent.platform import PlatformAgent
from base_agent.user_agent import UserAgent
from base_agent.worker_factory import WorkerFactory

__version__ = "0.1.0"

__all__ = ["AgentFactory", "BaseAgent", "PlatformAgent", "UserAgent", "WorkerFactory"]
```

3. Edit `agents/src/base_agent/factory.py` — add import of UserAgent at top (after PlatformAgent import) and add the `user_agent()` method after `platform_agent()`:

Add this import after line 12 (`from base_agent.platform import PlatformAgent`):
```python
from base_agent.user_agent import UserAgent
```

Add this method after the `platform_agent()` method (after line 125):
```python
    def user_agent(self) -> UserAgent:
        """Create the user agent for UI-driven operations.

        The user agent shares the platform agent's keys and identity.

        Returns:
            A UserAgent initialized with the platform keypair.

        Raises:
            KeyError: If "platform" is not in the roster.
        """
        config = self._load_config("platform")
        return UserAgent(config)
```

4. Verify: `cd agents && uv run python -c "from base_agent import UserAgent; print(UserAgent)"`
   Expected: `<class 'base_agent.user_agent.UserAgent'>`

5. Commit: `git add agents/src/base_agent/user_agent.py agents/src/base_agent/__init__.py agents/src/base_agent/factory.py && git commit -m "feat: add UserAgent subclass of PlatformAgent for UI-driven operations"`

---

## Phase 2: Add base-agent Dependency to UI Service

### Files to modify:
- `services/ui/pyproject.toml`

### Steps:

1. Edit `services/ui/pyproject.toml`:
   - Add `"base-agent",` to the `dependencies` list (after `"service-commons",`)
   - Add `base-agent = { path = "../../agents", editable = true }` to the `[tool.uv.sources]` section
   - Add a new mypy overrides block:
     ```toml
     [[tool.mypy.overrides]]
     module = "base_agent.*"
     ignore_missing_imports = true
     ```
   - In `[tool.deptry.per_rule_ignores]` DEP002 list, add `"cryptography"` to the list

2. Run: `cd services/ui && uv sync --all-extras`

3. Verify: `cd services/ui && uv run python -c "from base_agent import UserAgent, AgentFactory; print('OK')"`
   Expected: `OK`

4. Commit: `git add services/ui/pyproject.toml services/ui/uv.lock && git commit -m "feat: add base-agent dependency to UI service"`

---

## Phase 3: Add UserAgent Configuration

### Files to modify:
- `services/ui/config.yaml`
- `services/ui/src/ui_service/config.py`

### Steps:

1. Append to the end of `services/ui/config.yaml`:
```yaml

user_agent:
  agent_config_path: "../../agents/config.yaml"
```

2. Edit `services/ui/src/ui_service/config.py`:
   - Add a new `UserAgentConfig` class after `RequestConfig`:
     ```python
     class UserAgentConfig(BaseModel):
         """User agent configuration."""

         model_config = ConfigDict(extra="forbid")
         agent_config_path: str
     ```
   - Add `user_agent: UserAgentConfig` field to the `Settings` class (after `request: RequestConfig`)

3. Verify: `cd services/ui && uv run python -c "from ui_service.config import get_settings; s = get_settings(); print(s.user_agent.agent_config_path)"`
   Expected: `../../agents/config.yaml`

4. Commit: `git add services/ui/config.yaml services/ui/src/ui_service/config.py && git commit -m "feat: add user_agent configuration section to UI service"`

---

## Phase 4: Add UserAgent to AppState and Lifespan

### Files to modify:
- `services/ui/src/ui_service/core/state.py`
- `services/ui/src/ui_service/core/lifespan.py`

### Steps:

1. Edit `services/ui/src/ui_service/core/state.py`:
   - Add `from base_agent import UserAgent` inside the `TYPE_CHECKING` block (after the aiosqlite import)
   - Add `user_agent: UserAgent | None = field(default=None, repr=False)` to AppState (after the `db` field)

2. Edit `services/ui/src/ui_service/core/lifespan.py` to add UserAgent initialization.

   Add `from pathlib import Path` to imports.

   Inside the `lifespan` function, AFTER the database connection block and BEFORE the `logger.info("Service starting", ...)` line, add:

```python
    # Initialize UserAgent for UI-driven task operations
    try:
        from base_agent import AgentFactory

        config_path = Path(settings.user_agent.agent_config_path)
        if not config_path.is_absolute():
            config_path = Path.cwd() / config_path
        factory = AgentFactory(config_path=config_path.resolve())
        user_agent = factory.user_agent()
        await user_agent.register()
        state.user_agent = user_agent
        logger.info(
            "UserAgent initialized",
            extra={"agent_id": user_agent.agent_id, "name": user_agent.name},
        )
    except Exception as exc:
        logger.error(
            "UserAgent initialization failed — proxy endpoints will be unavailable",
            extra={"error": str(exc)},
        )
```

   In the SHUTDOWN section, BEFORE `if state.db is not None:`, add:
```python
    if state.user_agent is not None:
        await state.user_agent.close()
        logger.info("UserAgent closed")
```

3. Commit: `git add services/ui/src/ui_service/core/state.py services/ui/src/ui_service/core/lifespan.py && git commit -m "feat: instantiate UserAgent in UI service lifespan"`

---

## Phase 5: Create Proxy Schemas

### Files to modify:
- `services/ui/src/ui_service/schemas.py`

### Steps:

1. Append these models to the end of `services/ui/src/ui_service/schemas.py`:

```python


# ---------------------------------------------------------------------------
# Proxy (UserAgent task lifecycle)
# ---------------------------------------------------------------------------
class CreateTaskRequest(BaseModel):
    """Request to create a new task via UserAgent."""

    model_config = ConfigDict(extra="forbid")
    title: str = Field(min_length=1, max_length=200)
    spec: str = Field(min_length=1, max_length=10000)
    reward: int = Field(gt=0)
    bidding_deadline_seconds: int = Field(gt=0)
    execution_deadline_seconds: int = Field(gt=0)
    review_deadline_seconds: int = Field(gt=0)


class FileDisputeRequest(BaseModel):
    """Request to file a dispute on a task."""

    model_config = ConfigDict(extra="forbid")
    reason: str = Field(min_length=1, max_length=5000)


class ProxyIdentityResponse(BaseModel):
    """Response for proxy identity endpoint."""

    model_config = ConfigDict(extra="forbid")
    agent_id: str


class ProxyTaskResponse(BaseModel):
    """Generic proxy response wrapping task-board response data."""

    model_config = ConfigDict(extra="forbid")
    task_id: str
    status: str
```

2. Commit: `git add services/ui/src/ui_service/schemas.py && git commit -m "feat: add proxy request/response schemas for task lifecycle"`

---

## Phase 6: Create Proxy Service Layer

### Files to create:
- `services/ui/src/ui_service/services/proxy.py`

### Steps:

1. Create `services/ui/src/ui_service/services/proxy.py` with this exact content:

```python
"""Proxy service — delegates task lifecycle operations to UserAgent."""

from __future__ import annotations

from typing import Any

from service_commons.exceptions import ServiceError

from ui_service.core.state import get_app_state


def _get_user_agent() -> Any:
    """Get the UserAgent from app state, raising ServiceError if unavailable."""
    state = get_app_state()
    if state.user_agent is None:
        raise ServiceError(
            "user_agent_unavailable",
            "UserAgent is not initialized. Task lifecycle operations are unavailable.",
            503,
            {},
        )
    return state.user_agent


async def get_identity() -> str:
    """Return the UserAgent's agent_id."""
    agent = _get_user_agent()
    if agent.agent_id is None:
        raise ServiceError(
            "user_agent_not_registered",
            "UserAgent is not registered with the Identity service.",
            503,
            {},
        )
    return agent.agent_id


async def create_task(
    title: str,
    spec: str,
    reward: int,
    bidding_deadline_seconds: int,
    execution_deadline_seconds: int,
    review_deadline_seconds: int,
) -> dict[str, Any]:
    """Post a new task via UserAgent."""
    agent = _get_user_agent()
    try:
        return await agent.post_task(
            title=title,
            spec=spec,
            reward=reward,
            bidding_deadline_seconds=bidding_deadline_seconds,
            execution_deadline_seconds=execution_deadline_seconds,
            review_deadline_seconds=review_deadline_seconds,
        )
    except Exception as exc:
        raise ServiceError(
            "task_creation_failed",
            f"Failed to create task: {exc}",
            502,
            {},
        ) from exc


async def accept_bid(task_id: str, bid_id: str) -> dict[str, Any]:
    """Accept a bid on a task via UserAgent."""
    agent = _get_user_agent()
    try:
        return await agent.accept_bid(task_id=task_id, bid_id=bid_id)
    except Exception as exc:
        raise ServiceError(
            "bid_acceptance_failed",
            f"Failed to accept bid: {exc}",
            502,
            {},
        ) from exc


async def approve_task(task_id: str) -> dict[str, Any]:
    """Approve a submitted task via UserAgent."""
    agent = _get_user_agent()
    try:
        return await agent.approve_task(task_id=task_id)
    except Exception as exc:
        raise ServiceError(
            "task_approval_failed",
            f"Failed to approve task: {exc}",
            502,
            {},
        ) from exc


async def file_dispute(task_id: str, reason: str) -> dict[str, Any]:
    """File a dispute on a task via UserAgent."""
    agent = _get_user_agent()
    try:
        return await agent.dispute_task(task_id=task_id, reason=reason)
    except Exception as exc:
        raise ServiceError(
            "dispute_filing_failed",
            f"Failed to file dispute: {exc}",
            502,
            {},
        ) from exc
```

2. Commit: `git add services/ui/src/ui_service/services/proxy.py && git commit -m "feat: add proxy service layer for UserAgent task operations"`

---

## Phase 7: Create Proxy Router and Register It

### Files to create/modify:
- Create: `services/ui/src/ui_service/routers/proxy.py`
- Modify: `services/ui/src/ui_service/app.py`

### Steps:

1. Create `services/ui/src/ui_service/routers/proxy.py` with this exact content:

```python
"""Proxy route handlers — task lifecycle via UserAgent."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter

from ui_service.schemas import (
    CreateTaskRequest,
    FileDisputeRequest,
    ProxyIdentityResponse,
)
from ui_service.services import proxy as proxy_service

router = APIRouter()


@router.get("/proxy/identity")
async def get_identity() -> ProxyIdentityResponse:
    """Return the UserAgent's agent_id."""
    agent_id = await proxy_service.get_identity()
    return ProxyIdentityResponse(agent_id=agent_id)


@router.post("/proxy/tasks")
async def create_task(body: CreateTaskRequest) -> dict[str, Any]:
    """Create a new task via UserAgent."""
    return await proxy_service.create_task(
        title=body.title,
        spec=body.spec,
        reward=body.reward,
        bidding_deadline_seconds=body.bidding_deadline_seconds,
        execution_deadline_seconds=body.execution_deadline_seconds,
        review_deadline_seconds=body.review_deadline_seconds,
    )


@router.post("/proxy/tasks/{task_id}/bids/{bid_id}/accept")
async def accept_bid(task_id: str, bid_id: str) -> dict[str, Any]:
    """Accept a bid on a task."""
    return await proxy_service.accept_bid(task_id=task_id, bid_id=bid_id)


@router.post("/proxy/tasks/{task_id}/approve")
async def approve_task(task_id: str) -> dict[str, Any]:
    """Approve a submitted task."""
    return await proxy_service.approve_task(task_id=task_id)


@router.post("/proxy/tasks/{task_id}/dispute")
async def file_dispute(task_id: str, body: FileDisputeRequest) -> dict[str, Any]:
    """File a dispute on a task."""
    return await proxy_service.file_dispute(task_id=task_id, reason=body.reason)
```

2. Edit `services/ui/src/ui_service/app.py`:
   - Update the import on line 14 to include `proxy`:
     ```python
     from ui_service.routers import agents, events, health, metrics, proxy, quarterly, tasks
     ```
   - Add this line after `app.include_router(quarterly.router, prefix="/api", tags=["Quarterly"])`:
     ```python
     app.include_router(proxy.router, prefix="/api", tags=["Proxy"])
     ```

3. Commit: `git add services/ui/src/ui_service/routers/proxy.py services/ui/src/ui_service/app.py && git commit -m "feat: add proxy router for task lifecycle endpoints"`

---

## Phase 8: Clean Up shared.js

### Files to modify:
- `services/ui/data/web/assets/shared.js`

### Steps:

1. In `services/ui/data/web/assets/shared.js`:
   - Delete the comment block on lines 314-316 (the "Backward-compat" comment)
   - Delete the entire `perturbEconomy` function (lines 317-341)
   - In the `window.ATE` export object, delete these two lines:
     ```javascript
     // Backward-compat for task.js demo
     perturbEconomy: perturbEconomy,
     startEconomyPerturbation: perturbEconomy,
     ```
     (lines 428-430)

2. Commit: `git add services/ui/data/web/assets/shared.js && git commit -m "chore: remove perturbEconomy backward-compat from shared.js"`

---

## Phase 9: Update task.html

### Files to modify:
- `services/ui/data/web/task.html`

### Steps:

1. Change `<title>ATE — Task Lifecycle Demo</title>` to `<title>ATE — Task Lifecycle</title>` (line 6)

2. Delete the CSS rules for demo controls (lines 220-236 in the `<style>` block):
   ```css
   .demo-controls { ... }
   .demo-controls .btn { ... }
   .demo-step-label { ... }
   .demo-progress { ... }
   .demo-progress-fill { ... }
   ```

3. Add these CSS rules after the `.phase-step.pending` rule (around line 53):
   ```css
   .phase-step.completed, .phase-step.active { cursor: pointer; }
   .phase-step.pending { cursor: not-allowed; }
   ```

4. Delete the entire demo controls HTML section (lines 312-321):
   ```html
   <!-- ═══ DEMO CONTROLS ══════════════════════════════════════════ -->
   <div class="demo-controls">
     ...
   </div>
   ```

5. Commit: `git add services/ui/data/web/task.html && git commit -m "chore: remove demo controls from task.html, update title"`

---

## Phase 10: Rewrite task.js

### Files to rewrite:
- `services/ui/data/web/assets/task.js`

### Steps:

1. Replace the ENTIRE contents of `services/ui/data/web/assets/task.js` with the new data-driven code from the implementation plan at `docs/plans/2026-03-02-task-view-live-wiring-plan.md` (Task 10, Step 1). Copy the JavaScript code exactly as written there.

   The new file should be approximately 400 lines. It contains:
   - A state object with mode, taskId, task, myAgentId, currentPhase, maxPhase, sseSource
   - A `taskStatusToPhase()` mapping function
   - Phase renderers: renderCreateForm, renderPostPhase, renderBidPhase, renderContractPhase, renderDeliverPhase, renderReviewPhase, renderRulingPhase, renderSettlePhase
   - SSE integration via `ATE.connectSSE()`
   - Event feed via `ATE.mapEventToFeed()`
   - Form bindings for task creation, bid acceptance, approval, dispute
   - Phase strip click navigation
   - URL parameter support (`?task_id=...`)
   - Ticker initialization via `ATE.buildBottomTicker()`

2. Commit: `git add services/ui/data/web/assets/task.js && git commit -m "feat: rewrite task.js with data-driven state machine replacing scripted demo"`

---

## Phase 11: Run CI

### Steps:

1. Run: `cd services/ui && just ci-quiet`
2. If there are failures, fix them. Common issues:
   - Ruff formatting: run `cd services/ui && just code-format`
   - Import ordering: run `cd services/ui && just code-format`
   - Mypy type errors: add type annotations or fix type mismatches
   - Spelling errors: fix the spelling or add to codespell ignore list
3. After fixing, re-run: `cd services/ui && just ci-quiet`
4. Expected: all checks pass
5. If CI passes, commit any fixes: `git add -A && git commit -m "fix: resolve CI issues from task view live wiring"`

---

## Phase 12: Final Verification

1. Run: `just ci-all-quiet` (from project root)
2. Expected: all checks pass for all services
3. If there are failures in other services (agents package changed), fix them.
