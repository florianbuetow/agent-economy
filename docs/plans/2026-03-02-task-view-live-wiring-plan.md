# Task View Live Wiring — Implementation Plan

> **For Claude:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task.

**Goal:** Replace the scripted task lifecycle demo with a live, data-driven view that creates real tasks, displays real data, and receives real-time SSE updates.

**Architecture:** A `UserAgent` (subclass of `PlatformAgent`) in the UI service signs JWS requests to the task-board API. The frontend state machine maps task status to phases and updates reactively via SSE. All existing CSS classes and visual components are preserved.

**Tech Stack:** Python/FastAPI (backend proxy), vanilla JavaScript (frontend), SSE (real-time), Ed25519 JWS (authentication)

**Design Doc:** `docs/plans/2026-03-02-task-view-live-wiring-design.md`

---

### Task 1: Create UserAgent Class

**Files:**
- Create: `agents/src/base_agent/user_agent.py`
- Modify: `agents/src/base_agent/__init__.py:10`
- Modify: `agents/src/base_agent/factory.py:115-125`

**Step 1: Write UserAgent class**

Create `agents/src/base_agent/user_agent.py`:

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

**Step 2: Export UserAgent from package**

Edit `agents/src/base_agent/__init__.py`:

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

**Step 3: Add user_agent() factory method**

Edit `agents/src/base_agent/factory.py` — add after `platform_agent()` method (after line 125):

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

Also add import at the top of `factory.py` (after the PlatformAgent import on line 12):

```python
from base_agent.user_agent import UserAgent
```

**Step 4: Verify agents package still imports cleanly**

Run: `cd agents && uv run python -c "from base_agent import UserAgent; print(UserAgent)"`
Expected: `<class 'base_agent.user_agent.UserAgent'>`

**Step 5: Commit**

```bash
git add agents/src/base_agent/user_agent.py agents/src/base_agent/__init__.py agents/src/base_agent/factory.py
git commit -m "feat: add UserAgent subclass of PlatformAgent for UI-driven operations"
```

---

### Task 2: Add base-agent Dependency to UI Service

**Files:**
- Modify: `services/ui/pyproject.toml:6-14`
- Modify: `services/ui/pyproject.toml:38-39` (tool.uv.sources)

**Step 1: Add base-agent to dependencies and sources**

Edit `services/ui/pyproject.toml` — add `"base-agent"` to the `dependencies` list:

```toml
dependencies = [
    "fastapi>=0.115.0",
    "uvicorn>=0.34.0",
    "starlette>=0.47.2",
    "pydantic>=2.10.0",
    "service-commons",
    "base-agent",
    "aiosqlite>=0.20.0",
    "sse-starlette>=2.2.1",
]
```

Add `base-agent` to `[tool.uv.sources]`:

```toml
[tool.uv.sources]
service-commons = { path = "../../libs/service-commons", editable = true }
base-agent = { path = "../../agents", editable = true }
```

Add mypy override for `base_agent`:

```toml
[[tool.mypy.overrides]]
module = "base_agent.*"
ignore_missing_imports = true
```

Add deptry ignore for `cryptography` (transitive dependency from base-agent):

In `[tool.deptry.per_rule_ignores]` section, add `"cryptography"` to the `DEP002` list.

**Step 2: Sync the environment**

Run: `cd services/ui && uv sync --all-extras`
Expected: Successful resolution with base-agent installed

**Step 3: Verify import works**

Run: `cd services/ui && uv run python -c "from base_agent import UserAgent, AgentFactory; print('OK')"`
Expected: `OK`

**Step 4: Commit**

```bash
git add services/ui/pyproject.toml services/ui/uv.lock
git commit -m "feat: add base-agent dependency to UI service"
```

---

### Task 3: Add UserAgent Configuration

**Files:**
- Modify: `services/ui/config.yaml:28-29`
- Modify: `services/ui/src/ui_service/config.py:81-96`

**Step 1: Add user_agent section to config.yaml**

Add to the end of `services/ui/config.yaml`:

```yaml
user_agent:
  agent_config_path: "../../agents/config.yaml"
```

**Step 2: Add UserAgentSettings to config.py**

Add a new model class after `RequestConfig` (after line 78) in `services/ui/src/ui_service/config.py`:

```python
class UserAgentConfig(BaseModel):
    """User agent configuration."""

    model_config = ConfigDict(extra="forbid")
    agent_config_path: str
```

Add `user_agent` field to the `Settings` class:

```python
class Settings(BaseModel):
    """
    Root configuration container.

    All fields are REQUIRED. No defaults exist.
    Missing fields cause immediate startup failure.
    """

    model_config = ConfigDict(extra="forbid")
    service: ServiceConfig
    server: ServerConfig
    logging: LoggingConfig
    database: DatabaseConfig
    sse: SSEConfig
    frontend: FrontendConfig
    request: RequestConfig
    user_agent: UserAgentConfig
```

**Step 3: Verify config loads**

Run: `cd services/ui && uv run python -c "from ui_service.config import get_settings; s = get_settings(); print(s.user_agent.agent_config_path)"`
Expected: `../../agents/config.yaml`

**Step 4: Commit**

```bash
git add services/ui/config.yaml services/ui/src/ui_service/config.py
git commit -m "feat: add user_agent configuration section to UI service"
```

---

### Task 4: Add UserAgent to AppState and Lifespan

**Files:**
- Modify: `services/ui/src/ui_service/core/state.py:1-18`
- Modify: `services/ui/src/ui_service/core/lifespan.py:1-60`

**Step 1: Add user_agent field to AppState**

Edit `services/ui/src/ui_service/core/state.py`:

```python
"""Application state management."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import aiosqlite

    from base_agent import UserAgent


@dataclass
class AppState:
    """Runtime application state."""

    start_time: datetime = field(default_factory=lambda: datetime.now(UTC))
    db: aiosqlite.Connection | None = field(default=None, repr=False)
    user_agent: UserAgent | None = field(default=None, repr=False)

    @property
    def uptime_seconds(self) -> float:
        """Calculate uptime in seconds."""
        return (datetime.now(UTC) - self.start_time).total_seconds()

    @property
    def started_at(self) -> str:
        """ISO format start time."""
        return self.start_time.isoformat(timespec="seconds").replace("+00:00", "Z")
```

The rest of the file (global state container, get/init/reset functions) stays unchanged.

**Step 2: Instantiate UserAgent in lifespan**

Edit `services/ui/src/ui_service/core/lifespan.py`:

```python
"""Application lifecycle management."""

from __future__ import annotations

from contextlib import asynccontextmanager
from pathlib import Path
from typing import TYPE_CHECKING

import aiosqlite

from ui_service.config import get_settings
from ui_service.core.state import init_app_state
from ui_service.logging import get_logger, setup_logging

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fastapi import FastAPI


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Manage application lifecycle."""
    # === STARTUP ===
    settings = get_settings()

    setup_logging(settings.logging.level, settings.service.name, settings.logging.directory)
    logger = get_logger(__name__)

    state = init_app_state()

    # Open read-only database connection
    db_uri = f"file:{settings.database.path}?mode=ro"
    try:
        db = await aiosqlite.connect(db_uri, uri=True)
        db.row_factory = aiosqlite.Row
        state.db = db
        logger.info("Database connection opened", extra={"path": settings.database.path})
    except (OSError, aiosqlite.Error) as exc:
        logger.error(
            "Database not available at startup",
            extra={"path": settings.database.path, "error": str(exc)},
        )

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

    logger.info(
        "Service starting",
        extra={
            "service": settings.service.name,
            "version": settings.service.version,
            "port": settings.server.port,
            "web_root": settings.frontend.web_root,
        },
    )

    yield  # Application runs here

    # === SHUTDOWN ===
    if state.user_agent is not None:
        await state.user_agent.close()
        logger.info("UserAgent closed")
    if state.db is not None:
        await state.db.close()
        logger.info("Database connection closed")
    logger.info("Service shutting down", extra={"uptime_seconds": state.uptime_seconds})
```

Note: The `from base_agent import AgentFactory` is inside the try block to avoid import failures breaking the entire service if the base-agent package has issues. The UserAgent is optional — if it fails to initialize, the service still runs (read-only mode).

**Step 3: Verify service starts**

Run: `cd services/ui && just run`
Expected: Service starts on port 8008. Log line: `UserAgent initialized agent_id=... name=platform`
(Ctrl+C to stop)

**Step 4: Commit**

```bash
git add services/ui/src/ui_service/core/state.py services/ui/src/ui_service/core/lifespan.py
git commit -m "feat: instantiate UserAgent in UI service lifespan"
```

---

### Task 5: Create Proxy Schemas

**Files:**
- Modify: `services/ui/src/ui_service/schemas.py:482`

**Step 1: Add proxy request/response models**

Append to the end of `services/ui/src/ui_service/schemas.py`:

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

**Step 2: Commit**

```bash
git add services/ui/src/ui_service/schemas.py
git commit -m "feat: add proxy request/response schemas for task lifecycle"
```

---

### Task 6: Create Proxy Service Layer

**Files:**
- Create: `services/ui/src/ui_service/services/proxy.py`

**Step 1: Write proxy service**

Create `services/ui/src/ui_service/services/proxy.py`:

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

**Step 2: Commit**

```bash
git add services/ui/src/ui_service/services/proxy.py
git commit -m "feat: add proxy service layer for UserAgent task operations"
```

---

### Task 7: Create Proxy Router

**Files:**
- Create: `services/ui/src/ui_service/routers/proxy.py`
- Modify: `services/ui/src/ui_service/app.py:14,39`

**Step 1: Write proxy router**

Create `services/ui/src/ui_service/routers/proxy.py`:

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

**Step 2: Register proxy router in app.py**

Edit `services/ui/src/ui_service/app.py`:

Update import line 14:

```python
from ui_service.routers import agents, events, health, metrics, proxy, quarterly, tasks
```

Add router registration after the quarterly router (after line 39):

```python
    app.include_router(proxy.router, prefix="/api", tags=["Proxy"])
```

**Step 3: Verify endpoint is registered**

Run: `cd services/ui && just run` (in background or another terminal)

Then:
```bash
curl -s http://localhost:8008/api/proxy/identity | python3 -m json.tool
```
Expected: `{"agent_id": "a-..."}` (the platform agent's ID)

**Step 4: Commit**

```bash
git add services/ui/src/ui_service/routers/proxy.py services/ui/src/ui_service/app.py
git commit -m "feat: add proxy router for task lifecycle endpoints"
```

---

### Task 8: Remove shared.js Backward-Compat Code

**Files:**
- Modify: `services/ui/data/web/assets/shared.js:314-341,428-430`

**Step 1: Remove perturbEconomy function and exports**

In `services/ui/data/web/assets/shared.js`:

1. Delete the `perturbEconomy` function (lines 317-341), including the comment above it (lines 314-316)
2. Delete the `perturbEconomy` and `startEconomyPerturbation` exports from the `window.ATE` object (lines 428-430)

The `window.ATE` export block should become:

```javascript
  window.ATE = {
    AGENTS: AGENTS,
    S: S,
    // Utilities
    pick: pick,
    randHex: randHex,
    timeAgo: timeAgo,
    sparkData: sparkData,
    renderSparkSVG: renderSparkSVG,
    genSparkline: genSparkline,
    animateCounter: animateCounter,
    agentColor: agentColor,
    // API client
    fetchMetrics: fetchMetrics,
    fetchAgents: fetchAgents,
    fetchEvents: fetchEvents,
    connectSSE: connectSSE,
    mapEventToFeed: mapEventToFeed,
    startMetricsPolling: startMetricsPolling,
    // Ticker builders
    buildTopTicker: buildTopTicker,
    buildBottomTicker: buildBottomTicker
  };
```

**Step 2: Commit**

```bash
git add services/ui/data/web/assets/shared.js
git commit -m "chore: remove perturbEconomy backward-compat from shared.js"
```

---

### Task 9: Update task.html

**Files:**
- Modify: `services/ui/data/web/task.html`

**Step 1: Update title**

Change line 6 from:
```html
<title>ATE — Task Lifecycle Demo</title>
```
to:
```html
<title>ATE — Task Lifecycle</title>
```

**Step 2: Remove demo-controls CSS**

Delete the CSS rules for `.demo-controls`, `.demo-step-label`, `.demo-progress`, `.demo-progress-fill` (lines 220-236 in the `<style>` block).

**Step 3: Remove demo controls HTML**

Delete lines 312-321 (the entire `<!-- ═══ DEMO CONTROLS ═══... -->` section):

```html
<!-- ═══ DEMO CONTROLS ══════════════════════════════════════════ -->
<div class="demo-controls">
  <button class="btn btn-cyan" id="btn-prev" disabled>← PREV</button>
  <div class="demo-progress">
    <div class="demo-progress-fill" id="progress-fill" style="width:0%"></div>
  </div>
  <span class="demo-step-label" id="step-label">Step 1/12 — Create Task</span>
  <button class="btn btn-cyan solid" id="btn-next">NEXT →</button>
  <button class="btn btn-amber" id="btn-auto">▶ AUTO</button>
</div>
```

**Step 4: Add phase-step click handler class**

The phase strip already has `data-phase` attributes. Add a `cursor: pointer` rule for clickable phases:

```css
.phase-step.completed, .phase-step.active { cursor: pointer; }
.phase-step.pending { cursor: not-allowed; }
```

Add this after the existing `.phase-step.pending` rule (around line 53).

**Step 5: Commit**

```bash
git add services/ui/data/web/task.html
git commit -m "chore: remove demo controls from task.html, update title"
```

---

### Task 10: Rewrite task.js — Data-Driven Task View

**Files:**
- Rewrite: `services/ui/data/web/assets/task.js`

This is the largest change. The entire 562-line scripted demo is replaced with a data-driven state machine.

**Step 1: Write new task.js**

Replace the entire contents of `services/ui/data/web/assets/task.js` with:

```javascript
(function() {
  'use strict';
  var ATE = window.ATE;

  // ── State ──────────────────────────────────────────────────
  var state = {
    mode: 'create',       // 'create' | 'view'
    taskId: null,
    task: null,            // Full drilldown from GET /api/tasks/{task_id}
    myAgentId: null,
    currentPhase: 0,
    maxPhase: 0,
    sseSource: null
  };

  // ── Phase mapping ──────────────────────────────────────────
  var PHASE_LABELS = ['Post', 'Bid', 'Contract', 'Deliver', 'Review', 'Ruling', 'Settle'];

  function taskStatusToPhase(task) {
    if (!task) return 0;
    var s = task.status;
    var bidCount = task.bids ? task.bids.length : 0;
    var assetCount = task.assets ? task.assets.length : 0;

    if (s === 'open' && bidCount === 0) return 0;
    if (s === 'open' && bidCount > 0) return 1;
    if (s === 'accepted' && assetCount === 0) return 2;
    if (s === 'accepted' && assetCount > 0) return 3;
    if (s === 'submitted') return 4;
    if (s === 'disputed') return 4;
    if (s === 'ruled') return task.feedback && task.feedback.length > 0 ? 6 : 5;
    if (s === 'approved') return 6;
    return 0;
  }

  function statusBadge(task) {
    if (!task) return { text: 'DRAFT', cls: 'status-open' };
    var map = {
      'open': { text: 'OPEN', cls: 'status-open' },
      'accepted': { text: 'ACTIVE', cls: 'status-active' },
      'submitted': { text: 'SUBMITTED', cls: 'status-submitted' },
      'disputed': { text: 'DISPUTED', cls: 'status-disputed' },
      'ruled': { text: 'RULED', cls: 'status-ruled' },
      'approved': { text: 'APPROVED', cls: 'status-approved' },
      'cancelled': { text: 'CANCELLED', cls: 'status-open' },
      'expired': { text: 'EXPIRED', cls: 'status-open' }
    };
    return map[task.status] || { text: task.status.toUpperCase(), cls: 'status-open' };
  }

  // ── Escrow bar ─────────────────────────────────────────────
  function escrowBar(task, settled) {
    if (!task) return '';
    if (settled) {
      return '<div class="escrow-bar" style="background:var(--green-dim)">' +
        '<span class="escrow-icon">&#10003;</span>' +
        '<span class="escrow-label">Escrow</span>' +
        '<span class="escrow-amount" style="color:var(--green)">' + task.reward + ' &copy; RELEASED</span>' +
        '<span class="escrow-status">SETTLED</span>' +
        '</div>';
    }
    var frozen = task.status === 'disputed';
    var icon = frozen ? '&#9878;&#65039;' : '&#128274;';
    var statusText = frozen ? 'FROZEN &middot; DISPUTE ACTIVE' : 'LOCKED';
    var statusStyle = frozen ? ' style="color:var(--red)"' : '';
    return '<div class="escrow-bar">' +
      '<span class="escrow-icon">' + icon + '</span>' +
      '<span class="escrow-label">Escrow</span>' +
      '<span class="escrow-amount">' + task.reward + ' &copy;</span>' +
      '<span class="escrow-status"' + statusStyle + '>' + statusText + '</span>' +
      '</div>';
  }

  // ── Phase renderers ────────────────────────────────────────
  function renderCreateForm() {
    return '<div class="card">' +
      '<div class="card-header"><span class="card-label">Create New Task</span></div>' +
      '<div class="card-body">' +
      '<div class="form-group">' +
        '<label class="form-label">Title</label>' +
        '<input class="form-input" id="f-title" type="text" placeholder="Task title" maxlength="200">' +
      '</div>' +
      '<div class="form-group">' +
        '<label class="form-label">Specification</label>' +
        '<textarea class="form-textarea" id="f-spec" placeholder="Detailed task specification..." maxlength="10000"></textarea>' +
      '</div>' +
      '<div class="form-row">' +
        '<div class="form-group">' +
          '<label class="form-label">Reward (&copy;)</label>' +
          '<input class="form-input" id="f-reward" type="number" min="1" placeholder="100">' +
        '</div>' +
        '<div class="form-group">' +
          '<label class="form-label">Bidding Deadline (s)</label>' +
          '<input class="form-input" id="f-bid-dl" type="number" min="1" placeholder="120">' +
        '</div>' +
      '</div>' +
      '<div class="form-row">' +
        '<div class="form-group">' +
          '<label class="form-label">Execution Deadline (s)</label>' +
          '<input class="form-input" id="f-exec-dl" type="number" min="1" placeholder="300">' +
        '</div>' +
        '<div class="form-group">' +
          '<label class="form-label">Review Deadline (s)</label>' +
          '<input class="form-input" id="f-rev-dl" type="number" min="1" placeholder="120">' +
        '</div>' +
      '</div>' +
      '<div style="margin-top:14px">' +
        '<button class="btn btn-cyan solid" id="btn-post-task">Post Task</button>' +
        '<span id="post-error" style="color:var(--red);font-size:11px;margin-left:12px"></span>' +
      '</div>' +
      '</div></div>';
  }

  function renderPostPhase(task) {
    return escrowBar(task) +
      '<div class="card">' +
      '<div class="card-header">' +
        '<span class="card-label">Task ' + task.task_id.slice(0, 12) + '</span>' +
        '<span style="font-size:9px;color:var(--text-dim)">Posted by ' + task.poster.name + '</span>' +
      '</div>' +
      '<div class="card-body">' +
        '<div class="task-detail-row"><span class="task-detail-key">Title</span><span class="task-detail-val">' + esc(task.title) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Reward</span><span class="task-detail-val" style="color:var(--green)">' + task.reward + ' &copy;</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Bidding Deadline</span><span class="task-detail-val">' + esc(task.deadlines.bidding_deadline) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Specification</span><span class="task-detail-val" style="font-size:10px;text-align:left;max-width:400px;color:var(--text-mid)">' + esc(task.spec) + '</span></div>' +
      '</div></div>' +
      '<div style="padding:12px 16px;font-size:9px;color:var(--text-dim);text-align:center;letter-spacing:1px">&#9203; WAITING FOR BIDS...</div>';
  }

  function renderBidPhase(task) {
    var bids = task.bids || [];
    var isMyTask = task.poster.agent_id === state.myAgentId;
    var html = escrowBar(task) +
      '<div class="card"><div class="card-header">' +
      '<span class="card-label">Bids on ' + task.task_id.slice(0, 12) + '</span>' +
      '<span style="font-size:9px;color:var(--text-dim)">' + bids.length + ' bid' + (bids.length !== 1 ? 's' : '') + ' received</span>' +
      '</div><div class="card-body no-pad">';
    bids.forEach(function(b) {
      var color = ATE.agentColor(b.bidder.agent_id);
      var initials = b.bidder.name.slice(0, 2).toUpperCase();
      var dq = b.bidder.delivery_quality;
      var total = (dq.extremely_satisfied || 0) + (dq.satisfied || 0) + (dq.dissatisfied || 0);
      var qualPct = total > 0 ? Math.round(((dq.extremely_satisfied || 0) + (dq.satisfied || 0)) / total * 100) : 0;
      html += '<div class="bid-row" style="animation:slide-in .3s ease-out">' +
        '<div class="bid-avatar" style="background:' + color + '22;color:' + color + ';border:1px solid ' + color + '44">' + initials + '</div>' +
        '<div class="bid-info"><div class="bid-name">' + esc(b.bidder.name) + '</div>' +
        '<div class="bid-meta">Quality: ' + qualPct + '% &middot; ' + esc(b.proposal.slice(0, 60)) + '</div></div>' +
        '<div class="bid-amount" style="color:var(--green)">' + task.reward + ' &copy;</div>' +
        '<div class="bid-actions">';
      if (isMyTask && !b.accepted && task.status === 'open') {
        html += '<button class="btn btn-green btn-accept-bid" data-bid-id="' + b.bid_id + '">Accept</button>';
      }
      html += '</div></div>';
    });
    html += '</div></div>';
    return html;
  }

  function renderContractPhase(task) {
    var worker = task.worker || { name: 'Unknown', agent_id: '' };
    var workerColor = ATE.agentColor(worker.agent_id);
    return escrowBar(task) +
      '<div class="card" style="border-color:var(--green)">' +
      '<div class="card-header" style="background:var(--green-dim)">' +
        '<span class="card-label" style="color:var(--green)">&#129309; Contract Signed</span>' +
        '<span style="font-size:9px;color:var(--green)">Platform co-signed</span>' +
      '</div><div class="card-body">' +
        '<div class="task-detail-row"><span class="task-detail-key">Task</span><span class="task-detail-val">' + task.task_id.slice(0, 12) + ' &mdash; ' + esc(task.title) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Poster</span><span class="task-detail-val" style="color:var(--cyan)">' + esc(task.poster.name) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Worker</span><span class="task-detail-val" style="color:' + workerColor + '">' + esc(worker.name) + '</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Reward</span><span class="task-detail-val" style="color:var(--green)">' + task.reward + ' &copy;</span></div>' +
        '<div class="task-detail-row"><span class="task-detail-key">Execution Deadline</span><span class="task-detail-val">' + esc(task.deadlines.execution_deadline || 'N/A') + '</span></div>' +
      '</div></div>';
  }

  function renderDeliverPhase(task) {
    var assets = task.assets || [];
    var html = escrowBar(task) +
      '<div class="card"><div class="card-header">' +
      '<span class="card-label">&#128230; Assets Delivered</span>' +
      '<span style="font-size:9px;color:var(--text-dim)">' + assets.length + ' file' + (assets.length !== 1 ? 's' : '') + '</span>' +
      '</div><div class="card-body">';
    if (assets.length === 0) {
      html += '<div style="font-size:10px;color:var(--text-dim);text-align:center">Worker is executing... no assets yet.</div>';
    }
    assets.forEach(function(a) {
      html += '<div class="task-detail-row"><span class="task-detail-key">' + esc(a.filename) + '</span><span class="task-detail-val">' + (a.size_bytes / 1024).toFixed(1) + ' KB</span></div>';
    });
    html += '</div></div>';
    return html;
  }

  function renderReviewPhase(task) {
    var isMyTask = task.poster.agent_id === state.myAgentId;
    var html = escrowBar(task);

    // Show assets/deliverable
    var assets = task.assets || [];
    if (assets.length > 0) {
      html += '<div class="card"><div class="card-header"><span class="card-label">&#128230; Deliverable</span></div><div class="card-body">';
      assets.forEach(function(a) {
        html += '<div class="task-detail-row"><span class="task-detail-key">' + esc(a.filename) + '</span><span class="task-detail-val">' + (a.size_bytes / 1024).toFixed(1) + ' KB</span></div>';
      });
      html += '</div></div>';
    }

    // Action buttons (only for poster, only if submitted)
    if (isMyTask && task.status === 'submitted') {
      html += '<div style="padding:12px 16px;display:flex;gap:10px">' +
        '<button class="btn btn-green" id="btn-approve">&#10003; Approve &mdash; Release Payout</button>' +
        '<button class="btn btn-red" id="btn-dispute-show">&#10007; Dispute</button>' +
        '</div>';
    }

    // Dispute panel (if disputed)
    if (task.dispute) {
      html += '<div class="dispute-panel">' +
        '<div class="dispute-label" style="color:var(--red)">&#128680; Dispute &mdash; Filed by ' + esc(task.poster.name) + '</div>' +
        '<div class="dispute-text">' + esc(task.dispute.reason) + '</div></div>';

      if (task.dispute.rebuttal) {
        var workerName = task.worker ? task.worker.name : 'Worker';
        html += '<div class="rebuttal-panel">' +
          '<div class="dispute-label" style="color:var(--amber)">&#128737; Rebuttal &mdash; Filed by ' + esc(workerName) + '</div>' +
          '<div class="dispute-text">' + esc(task.dispute.rebuttal.content) + '</div></div>';
      }
    }

    // Dispute form
    if (isMyTask && task.status === 'submitted') {
      html += '<div id="dispute-form" style="display:none;padding:12px 16px">' +
        '<div class="form-group"><label class="form-label">Dispute Reason</label>' +
        '<textarea class="form-textarea" id="f-dispute-reason" placeholder="Explain why the deliverable is unsatisfactory..."></textarea></div>' +
        '<button class="btn btn-red" id="btn-submit-dispute">Submit Dispute</button>' +
        '<span id="dispute-error" style="color:var(--red);font-size:11px;margin-left:12px"></span>' +
        '</div>';
    }

    return html;
  }

  function renderRulingPhase(task) {
    if (!task.dispute || !task.dispute.ruling) {
      return escrowBar(task) + '<div style="padding:20px;text-align:center;color:var(--text-dim)">Awaiting court ruling...</div>';
    }
    var ruling = task.dispute.ruling;
    return escrowBar(task) +
      '<div class="ruling-card" style="animation:scale-in .5s ease-out">' +
      '<div class="ruling-header">' +
        '<span class="gavel">&#9878;&#65039;</span>' +
        '<span class="ruling-title">Court Ruling &mdash; ' + task.task_id.slice(0, 12) + '</span>' +
      '</div><div class="ruling-body">' +
        '<div class="ruling-scores">' +
          '<div class="ruling-score"><div class="ruling-score-label">Worker Payout</div>' +
          '<div class="ruling-score-value" style="color:var(--green)">' + ruling.worker_pct + '%</div></div>' +
          '<div class="ruling-score"><div class="ruling-score-label">Poster Refund</div>' +
          '<div class="ruling-score-value" style="color:var(--amber)">' + (100 - ruling.worker_pct) + '%</div></div>' +
        '</div>' +
        '<div style="margin-bottom:8px"><span class="card-label">Ruling Summary</span></div>' +
        '<div class="ruling-reasoning">' + esc(ruling.summary) + '</div>' +
      '</div></div>';
  }

  function renderSettlePhase(task) {
    var html = escrowBar(task, true);

    // If there was a ruling, show payout breakdown
    if (task.dispute && task.dispute.ruling) {
      var ruling = task.dispute.ruling;
      var workerPayout = Math.round(task.reward * ruling.worker_pct / 100);
      var posterPayout = task.reward - workerPayout;
      html += '<div class="ruling-card"><div class="ruling-header">' +
        '<span class="gavel">&#9878;&#65039;</span><span class="ruling-title">Settlement</span>' +
        '</div><div class="ruling-body"><div class="ruling-payout">' +
        '<div class="payout-box"><div class="payout-agent">' + esc(task.poster.name) + ' (Poster)</div><div class="payout-amount" style="color:var(--green)">' + posterPayout + ' &copy;</div></div>' +
        '<div class="payout-box"><div class="payout-agent">' + esc((task.worker || {}).name || 'Worker') + ' (Worker)</div><div class="payout-amount" style="color:var(--amber)">' + workerPayout + ' &copy;</div></div>' +
        '</div></div></div>';
    }

    // Feedback
    var feedback = task.feedback || [];
    if (feedback.length > 0) {
      html += '<div class="card"><div class="card-header"><span class="card-label">&#128202; Feedback</span></div><div class="card-body no-pad">';
      feedback.forEach(function(f) {
        var stars = f.rating === 'extremely_satisfied' ? '&#9733;&#9733;&#9733;' : f.rating === 'satisfied' ? '&#9733;&#9733;&#9734;' : '&#9733;&#9734;&#9734;';
        html += '<div class="feedback-row">' +
          '<span class="feedback-from" style="color:var(--cyan)">' + esc(f.from_agent_name) + ' &rarr;</span>' +
          '<span class="feedback-stars">' + stars + '</span>' +
          '<span class="feedback-text">' + esc(f.comment || '') + '</span></div>';
      });
      html += '</div></div>';
    }

    html += '<div style="padding:20px;text-align:center">' +
      '<span style="font-size:11px;color:var(--green);font-weight:700;letter-spacing:1px">&#10003; TASK LIFECYCLE COMPLETE</span></div>';
    return html;
  }

  var RENDERERS = [renderPostPhase, renderBidPhase, renderContractPhase, renderDeliverPhase, renderReviewPhase, renderRulingPhase, renderSettlePhase];

  // ── Utility ────────────────────────────────────────────────
  function esc(str) {
    if (!str) return '';
    var div = document.createElement('div');
    div.appendChild(document.createTextNode(String(str)));
    return div.innerHTML;
  }

  // ── Phase strip rendering ──────────────────────────────────
  function renderPhaseStrip() {
    var steps = document.querySelectorAll('.phase-step');
    steps.forEach(function(el, i) {
      el.className = 'phase-step';
      if (i < state.maxPhase) {
        el.classList.add('completed');
        el.innerHTML = el.innerHTML.replace(/<span class="phase-check">.*<\/span>/, '');
        el.innerHTML += '<span class="phase-check">&#10003;</span>';
      } else if (i === state.currentPhase) {
        el.classList.add('active');
      } else {
        el.classList.add('pending');
      }
    });
  }

  // ── Main view update ───────────────────────────────────────
  function updateView() {
    var content = document.getElementById('phase-content');
    var panelTitle = document.getElementById('panel-title');
    var statusEl = document.getElementById('task-status');

    if (state.mode === 'create') {
      panelTitle.textContent = '&#128221; Post a New Task';
      statusEl.textContent = 'DRAFT';
      statusEl.className = 'status-badge status-open';
      content.innerHTML = renderCreateForm();
      state.currentPhase = 0;
      state.maxPhase = 0;
      renderPhaseStrip();
      bindCreateForm();
      return;
    }

    if (!state.task) {
      content.innerHTML = '<div style="padding:40px;text-align:center;color:var(--text-dim)">Loading task...</div>';
      return;
    }

    var phase = taskStatusToPhase(state.task);
    state.maxPhase = phase;
    if (state.currentPhase > phase) state.currentPhase = phase;

    var badge = statusBadge(state.task);
    panelTitle.textContent = PHASE_LABELS[state.currentPhase];
    statusEl.textContent = badge.text;
    statusEl.className = 'status-badge ' + badge.cls;

    if (state.currentPhase === 0 && phase === 0 && state.task.status === 'open') {
      content.innerHTML = renderPostPhase(state.task);
    } else {
      var renderer = RENDERERS[state.currentPhase];
      content.innerHTML = renderer ? renderer(state.task) : '';
    }

    renderPhaseStrip();
    bindActionButtons();
  }

  // ── Phase navigation ───────────────────────────────────────
  function navigateToPhase(phase) {
    if (phase < 0 || phase > state.maxPhase) return;
    state.currentPhase = phase;
    updateView();
  }

  document.getElementById('phase-strip').addEventListener('click', function(e) {
    var step = e.target.closest('.phase-step');
    if (!step) return;
    var phase = parseInt(step.getAttribute('data-phase'), 10);
    if (isNaN(phase)) return;
    navigateToPhase(phase);
  });

  // ── Form bindings ──────────────────────────────────────────
  function bindCreateForm() {
    var btn = document.getElementById('btn-post-task');
    if (!btn) return;
    btn.addEventListener('click', async function() {
      var title = document.getElementById('f-title').value.trim();
      var spec = document.getElementById('f-spec').value.trim();
      var reward = parseInt(document.getElementById('f-reward').value, 10);
      var bidDl = parseInt(document.getElementById('f-bid-dl').value, 10);
      var execDl = parseInt(document.getElementById('f-exec-dl').value, 10);
      var revDl = parseInt(document.getElementById('f-rev-dl').value, 10);
      var errEl = document.getElementById('post-error');

      if (!title || !spec || !reward || !bidDl || !execDl || !revDl) {
        errEl.textContent = 'All fields are required.';
        return;
      }

      btn.disabled = true;
      errEl.textContent = '';

      try {
        var resp = await fetch('/api/proxy/tasks', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: title,
            spec: spec,
            reward: reward,
            bidding_deadline_seconds: bidDl,
            execution_deadline_seconds: execDl,
            review_deadline_seconds: revDl
          })
        });
        if (!resp.ok) {
          var err = await resp.json().catch(function() { return {}; });
          throw new Error(err.message || 'Failed to create task (HTTP ' + resp.status + ')');
        }
        var data = await resp.json();
        state.taskId = data.task_id;
        state.mode = 'view';

        // Update URL without reload
        var url = new URL(window.location);
        url.searchParams.set('task_id', state.taskId);
        window.history.pushState({}, '', url);

        await refreshTaskData(state.taskId);
        connectTaskSSE(state.taskId);
      } catch (e) {
        errEl.textContent = e.message;
        btn.disabled = false;
      }
    });
  }

  // ── Action button bindings ─────────────────────────────────
  function bindActionButtons() {
    // Accept bid buttons
    document.querySelectorAll('.btn-accept-bid').forEach(function(btn) {
      btn.addEventListener('click', async function() {
        var bidId = this.getAttribute('data-bid-id');
        this.disabled = true;
        try {
          await fetch('/api/proxy/tasks/' + state.taskId + '/bids/' + bidId + '/accept', { method: 'POST' });
          await refreshTaskData(state.taskId);
        } catch (e) {
          console.error('Accept bid failed:', e);
          this.disabled = false;
        }
      });
    });

    // Approve button
    var approveBtn = document.getElementById('btn-approve');
    if (approveBtn) {
      approveBtn.addEventListener('click', async function() {
        this.disabled = true;
        try {
          await fetch('/api/proxy/tasks/' + state.taskId + '/approve', { method: 'POST' });
          await refreshTaskData(state.taskId);
        } catch (e) {
          console.error('Approve failed:', e);
          this.disabled = false;
        }
      });
    }

    // Show dispute form
    var disputeShowBtn = document.getElementById('btn-dispute-show');
    if (disputeShowBtn) {
      disputeShowBtn.addEventListener('click', function() {
        var form = document.getElementById('dispute-form');
        if (form) form.style.display = 'block';
      });
    }

    // Submit dispute
    var submitDisputeBtn = document.getElementById('btn-submit-dispute');
    if (submitDisputeBtn) {
      submitDisputeBtn.addEventListener('click', async function() {
        var reason = document.getElementById('f-dispute-reason').value.trim();
        var errEl = document.getElementById('dispute-error');
        if (!reason) { errEl.textContent = 'Reason is required.'; return; }
        this.disabled = true;
        errEl.textContent = '';
        try {
          var resp = await fetch('/api/proxy/tasks/' + state.taskId + '/dispute', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reason: reason })
          });
          if (!resp.ok) {
            var err = await resp.json().catch(function() { return {}; });
            throw new Error(err.message || 'Failed to file dispute');
          }
          await refreshTaskData(state.taskId);
        } catch (e) {
          errEl.textContent = e.message;
          this.disabled = false;
        }
      });
    }
  }

  // ── Data fetching ──────────────────────────────────────────
  async function refreshTaskData(taskId) {
    try {
      var resp = await fetch('/api/tasks/' + taskId);
      if (resp.ok) {
        state.task = await resp.json();
        var newPhase = taskStatusToPhase(state.task);
        state.currentPhase = newPhase;
        updateView();
      }
    } catch (e) {
      console.warn('[task] refreshTaskData error:', e.message);
    }
  }

  // ── SSE ────────────────────────────────────────────────────
  function connectTaskSSE(taskId) {
    if (state.sseSource) {
      state.sseSource.close();
    }
    state.sseSource = ATE.connectSSE(function(event) {
      if (event.task_id === taskId) {
        refreshTaskData(taskId);
        addRealFeedEvent(event);
      }
    });
  }

  // ── Event feed ─────────────────────────────────────────────
  function addRealFeedEvent(event) {
    var feed = ATE.mapEventToFeed(event);
    var scroll = document.getElementById('feed-scroll');
    if (!scroll) return;
    var timeStr = new Date(event.timestamp).toLocaleTimeString('en-US', { hour12: false, hour: '2-digit', minute: '2-digit', second: '2-digit' });
    var div = document.createElement('div');
    div.className = 'feed-item';
    div.innerHTML = '<span class="feed-badge ' + feed.badge + '">' + feed.type + '</span>' +
      '<span class="feed-text">' + feed.text + '</span>' +
      '<span class="feed-time">' + timeStr + '</span>';
    scroll.insertBefore(div, scroll.firstChild);
  }

  async function loadHistoricalEvents(taskId) {
    try {
      var data = await ATE.fetchEvents(50);
      var events = (data.events || []).filter(function(e) { return e.task_id === taskId; });
      events.reverse(); // oldest first so newest ends up on top
      events.forEach(addRealFeedEvent);
    } catch (e) {
      console.warn('[task] loadHistoricalEvents error:', e.message);
    }
  }

  // ── Bottom ticker ──────────────────────────────────────────
  function buildTicker() {
    if (ATE && typeof ATE.buildBottomTicker === 'function') {
      ATE.buildBottomTicker(document.getElementById('ticker-track'));
    }
  }

  // ── Boot ───────────────────────────────────────────────────
  async function init() {
    // Fetch metrics for ticker
    await ATE.fetchMetrics();
    await ATE.fetchAgents();
    buildTicker();

    // Get our identity
    try {
      var idResp = await fetch('/api/proxy/identity');
      if (idResp.ok) {
        var idData = await idResp.json();
        state.myAgentId = idData.agent_id;
      }
    } catch (e) {
      console.warn('[task] Could not fetch proxy identity:', e.message);
    }

    // Check URL for task_id
    var params = new URLSearchParams(window.location.search);
    var taskId = params.get('task_id');
    if (taskId) {
      state.taskId = taskId;
      state.mode = 'view';
      await refreshTaskData(taskId);
      await loadHistoricalEvents(taskId);
      connectTaskSSE(taskId);
    } else {
      state.mode = 'create';
      updateView();
    }

    // Start metrics polling for ticker updates
    ATE.startMetricsPolling(function() {
      buildTicker();
    }, 10000);
  }

  init();
})();
```

**Step 2: Verify page loads without errors**

1. Restart the UI service: `cd services/ui && just run`
2. Open http://localhost:8008/task.html
3. Expected: Task creation form visible (Title, Specification, Reward, 3 deadline fields, "Post Task" button)
4. Check browser console: no JavaScript errors

**Step 3: Commit**

```bash
git add services/ui/data/web/assets/task.js
git commit -m "feat: rewrite task.js with data-driven state machine replacing scripted demo"
```

---

### Task 11: End-to-End Smoke Test

This is a manual verification task. No code changes.

**Step 1: Ensure all services are running**

Run: `just start-all && just status`
Expected: All services healthy (ports 8001-8008)

**Step 2: Create a task from the UI**

1. Open http://localhost:8008/task.html
2. Fill in the form:
   - Title: "Test task from UI"
   - Specification: "Find the sum of 2 + 2"
   - Reward: 50
   - Bidding Deadline: 120
   - Execution Deadline: 300
   - Review Deadline: 120
3. Click "Post Task"
4. Expected: URL updates to `?task_id=t-...`, phase strip shows "Post" as active, escrow bar shows "50 © LOCKED", task details card visible

**Step 3: Verify SSE integration**

1. In another terminal, submit a bid on the task using the task-board API directly (or wait for an autonomous agent to bid)
2. Expected: The task view automatically updates — phase strip advances to "Bid", bid list appears

**Step 4: Verify phase navigation**

1. Click on the "Post" phase in the strip
2. Expected: Navigates back to the Post phase view
3. Click on a "pending" (grayed out) phase
4. Expected: Nothing happens — click is ignored

---

### Task 12: Run CI Checks

**Step 1: Run UI service CI**

Run: `cd services/ui && just ci-quiet`
Expected: All checks pass (ruff, mypy, pyright, bandit, etc.)

If there are failures, fix them before proceeding.

**Step 2: Run full project CI**

Run: `just ci-all-quiet`
Expected: All checks pass

**Step 3: Final commit if any fixes were needed**

```bash
git add -A
git commit -m "fix: resolve CI issues from task view live wiring"
```

---

## Summary of All Files Changed

| File | Action | Task |
|------|--------|------|
| `agents/src/base_agent/user_agent.py` | Create | 1 |
| `agents/src/base_agent/__init__.py` | Modify | 1 |
| `agents/src/base_agent/factory.py` | Modify | 1 |
| `services/ui/pyproject.toml` | Modify | 2 |
| `services/ui/config.yaml` | Modify | 3 |
| `services/ui/src/ui_service/config.py` | Modify | 3 |
| `services/ui/src/ui_service/core/state.py` | Modify | 4 |
| `services/ui/src/ui_service/core/lifespan.py` | Modify | 4 |
| `services/ui/src/ui_service/schemas.py` | Modify | 5 |
| `services/ui/src/ui_service/services/proxy.py` | Create | 6 |
| `services/ui/src/ui_service/routers/proxy.py` | Create | 7 |
| `services/ui/src/ui_service/app.py` | Modify | 7 |
| `services/ui/data/web/assets/shared.js` | Modify | 8 |
| `services/ui/data/web/task.html` | Modify | 9 |
| `services/ui/data/web/assets/task.js` | Rewrite | 10 |
