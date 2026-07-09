# Task View Live Wiring — Design Document

**Date:** 2026-03-02
**Status:** Approved
**Scope:** Remove scripted task demo, wire task view to real backend with SSE updates

## Problem

The task lifecycle view (`task.html` + `task.js`) is a fully scripted 11-step demo with hardcoded data (Alice, Bob, Carol, a sheep-tagging problem, a dispute, a ruling). It has PREV/NEXT/AUTO controls that step through pre-written HTML. No API calls are made — the page is completely self-contained.

We need to replace this with a live, data-driven view that:
1. Creates real tasks via the task-board API
2. Displays real task data from the backend
3. Receives real-time updates via SSE when task state changes
4. Supports full lifecycle control (accept bid, approve, dispute)
5. Only shows phases that have real data backing them

## Architecture

### User Agent

A new `UserAgent` class that subclasses `PlatformAgent`. It shares the platform agent's Ed25519 keypair and identity. All operations performed through the UI are signed as the platform agent.

```
UserAgent(PlatformAgent(BaseAgent))
    └── Same keys, same agent_id as platform agent
    └── Provides all TaskBoardMixin, BankMixin methods
    └── Instantiated by UI service on startup
```

The UserAgent is semantically distinct from PlatformAgent to allow future divergence (separate keys, separate permissions) while currently inheriting everything.

### Write Flow

```
Browser (JSON) → UI Service proxy endpoints → UserAgent signs JWS
    → Task-Board Service (validates JWS, enforces business rules)
    → Persists to DB → economy.db
```

The UI service calls the task-board service through regular API channels with proper JWS authentication. It does NOT call the DB gateway directly. This preserves all business logic validation (deadline checks, fund verification, status transitions).

### Read Flow

```
UI Service (read-only SQLite) ← economy.db
    → REST API → Browser
```

Unchanged from current implementation. The UI service reads from the shared database.

### Real-Time Update Flow

```
1. User posts task via UI form
2. UI service signs JWS with UserAgent, calls task-board POST /tasks
3. Task-board validates, persists, writes event to economy.db
4. SSE stream (GET /api/events/stream) picks up new event (1s poll)
5. Browser receives SSE event with matching task_id
6. Browser re-fetches GET /api/tasks/{task_id} for full state
7. Phase strip and content update to reflect new state
```

No new WebSocket or SSE endpoint needed. The existing `/api/events/stream` already streams all economy events. The frontend filters by task_id client-side.

### Test Agent (Future Scope)

A `TestAgent` class, also subclassing `PlatformAgent`, for integration tests. It simulates counterparty actions (bidding, delivering) by calling the DB gateway directly with privileged access. This enables end-to-end testing of the UI task flow without running the full autonomous agent fleet.

Not implemented in this design — flagged for a follow-up ticket.

## Component Details

### 1. UserAgent Class

**File:** `agents/src/base_agent/user_agent.py`

```python
class UserAgent(PlatformAgent):
    """Agent used by the UI service for human-driven task lifecycle operations.

    Inherits PlatformAgent's keys and identity. All operations appear as
    the platform agent. Provides the same task-board, bank, and reputation
    methods as any other agent.
    """
```

Initially a thin subclass. All needed methods (post_task, accept_bid, approve_task, file_dispute) are inherited from BaseAgent's mixins.

### 2. UI Service Backend Changes

#### Configuration

**`config.yaml` additions:**
```yaml
user_agent:
  agent_config_path: "../../agents/config.yaml"
```

**`config.py` additions:**
- `UserAgentSettings` model with `agent_config_path: str`

#### AppState

**`core/state.py` additions:**
- `user_agent: UserAgent | None` field

#### Startup

**`core/lifespan.py` additions:**
On startup:
1. Read `user_agent.agent_config_path` from settings
2. Create `AgentFactory(config_path=...)`
3. Instantiate `UserAgent` via factory (loads platform keys from `agents/data/keys/platform.key`)
4. Ensure agent is registered with Identity service
5. Store in `AppState.user_agent`

On shutdown:
1. Close the UserAgent's HTTP client

#### Proxy Router

**New file:** `routers/proxy.py` (prefix: `/api/proxy`)

| Endpoint | Method | Request Body | UserAgent Method |
|----------|--------|-------------|-----------------|
| `/api/proxy/tasks` | POST | `{title, spec, reward, bidding_deadline_seconds, execution_deadline_seconds, review_deadline_seconds}` | `user_agent.post_task(...)` |
| `/api/proxy/tasks/{task_id}/bids/{bid_id}/accept` | POST | `{}` | `user_agent.accept_bid(...)` |
| `/api/proxy/tasks/{task_id}/approve` | POST | `{}` | `user_agent.approve_task(...)` |
| `/api/proxy/tasks/{task_id}/dispute` | POST | `{reason}` | `user_agent.file_dispute(...)` |
| `/api/proxy/identity` | GET | — | Returns `{agent_id}` |

**New file:** `services/proxy.py`

Thin business logic layer that:
- Gets `user_agent` from AppState
- Calls the appropriate method
- Catches `httpx` / service errors and maps to ServiceError responses
- Returns the task-board response to the browser

#### Schemas

**`schemas.py` additions:**
- `CreateTaskRequest(title: str, spec: str, reward: int, bidding_deadline_seconds: int, execution_deadline_seconds: int, review_deadline_seconds: int)`
- `FileDisputeRequest(reason: str)`
- `ProxyIdentityResponse(agent_id: str)`

#### Dependencies

**`pyproject.toml` additions:**
- Path dependency on `base-agent` package from `../../agents`
- `cryptography` (transitive, needed by base-agent)
- `httpx` (already used by base-agent for outbound calls)

### 3. Frontend: task.js Rewrite

#### What Gets Removed

Everything from the current task.js:
- `TASK`, `BIDS`, `DELIVERABLE`, `DISPUTE_REASON`, `REBUTTAL`, `RULING` — hardcoded scenario data
- `STEPS[]` — linear 11-step array
- `FEED_EVENTS[]` — pre-scripted event feed
- `RENDERERS[]` — step-indexed renderer array
- `currentStep`, `autoTimer` — step navigation state
- `nextStep()`, `prevStep()`, `toggleAuto()`, `stopAuto()`, `resetDemo()` — navigation functions
- Arrow key / spacebar keyboard handlers
- `buildTicker()` with hardcoded fallback data

#### What Gets Preserved

All CSS classes and visual components remain identical:
- Phase strip (`.phase-step`, `.completed`, `.active`, `.pending`)
- Cards (`.card`, `.card-header`, `.card-body`)
- Escrow bar (`.escrow-bar`, `.escrow-amount`, `.escrow-status`)
- Bid rows (`.bid-row`, `.bid-avatar`, `.bid-info`, `.bid-amount`)
- Dispute/rebuttal panels (`.dispute-panel`, `.rebuttal-panel`)
- Ruling card (`.ruling-card`, `.ruling-scores`, `.ruling-payout`)
- Feedback rows (`.feedback-row`, `.feedback-stars`)
- Status badges (`.status-open`, `.status-active`, `.status-disputed`, etc.)
- All animations (fade-in, slide-in, scale-in, gavel-swing)
- Two-column layout (lifecycle-panel + feed-panel)
- Bottom ticker

#### New State Model

```javascript
const state = {
  mode: 'create',       // 'create' | 'view'
  taskId: null,          // Current task ID
  task: null,            // Full drilldown from GET /api/tasks/{task_id}
  myAgentId: null,       // From GET /api/proxy/identity
  currentPhase: 0,       // Currently displayed phase (0-6)
  sseSource: null,       // EventSource connection
};
```

#### Task Status → Phase Mapping

| Task Status | Condition | Phase | Label |
|------------|-----------|-------|-------|
| (no task) | — | 0 | Post |
| `open` | bid_count == 0 | 0 | Post (waiting for bids) |
| `open` | bid_count > 0 | 1 | Bid |
| `accepted` | no assets | 2 | Contract |
| `accepted` | has assets | 3 | Deliver |
| `submitted` | — | 4 | Review |
| `disputed` | no ruling | 4 | Review (disputed) |
| `ruled` | — | 5 | Ruling |
| `approved` | — | 6 | Settle |
| `ruled` (with feedback) | — | 6 | Settle |

#### View Modes

**Create mode** (default):
- Task creation form: Title (input), Spec (textarea), Reward (number), three deadline fields
- "Post Task" button → `POST /api/proxy/tasks`
- On success: set `state.taskId`, switch to view mode, connect SSE

**View mode** (after task creation or URL param `?task_id=...`):
- Phase strip shows actual task status
- Left panel renders phase-specific content from `state.task`
- Right panel shows real event feed
- Action buttons appear contextually:
  - Bid phase: "Accept" button on each bid (when user is poster)
  - Review phase: "Approve" / "Dispute" buttons (when user is poster, status=submitted)

#### Phase Renderers

Each function receives `state.task` and returns HTML using existing CSS classes:

- `renderPostPhase(task)` — Escrow bar + task details card (title, spec, reward, deadlines) + "Waiting for bids..."
- `renderBidPhase(task)` — Escrow bar + bid list from `task.bids[]` with Accept buttons
- `renderContractPhase(task)` — Escrow bar + contract card (poster, worker, bid amount, deadline, signatures)
- `renderDeliverPhase(task)` — Escrow bar + asset list from `task.assets[]`
- `renderReviewPhase(task)` — Escrow bar + deliverable display + Approve/Dispute buttons. If disputed: dispute panel + rebuttal panel
- `renderRulingPhase(task)` — Escrow bar + ruling card with scores and reasoning from `task.dispute.ruling`
- `renderSettlePhase(task)` — Settled escrow bar + payout breakdown + feedback display from `task.feedback[]`

#### Phase Strip Behavior

- Click a phase: navigates to it if data exists (phase ≤ maxReachedPhase)
- Phases beyond current state: grayed out with `.pending` class, click disabled
- On SSE update: auto-navigate to the new phase

#### SSE Integration

```javascript
function connectTaskSSE(taskId) {
  state.sseSource = ATE.connectSSE(function(event) {
    if (event.task_id === taskId) {
      refreshTaskData(taskId);
      addRealFeedEvent(event);
    }
  });
}

async function refreshTaskData(taskId) {
  const resp = await fetch('/api/tasks/' + taskId);
  if (resp.ok) {
    state.task = await resp.json();
    updateView();
  }
}
```

#### Event Feed

Right panel (`.feed-panel`):
- On task load: fetch historical events via `GET /api/events?task_id={task_id}&limit=50`
- On SSE event matching task_id: prepend to feed using `ATE.mapEventToFeed()`
- Reuse existing badge classes (badge-task, badge-bid, badge-contract, etc.)

### 4. task.html Changes

- **Remove:** `.demo-controls` div (lines 312-321) — PREV/NEXT/AUTO bar
- **Remove:** Related `<style>` rules for `.demo-controls`, `.demo-progress`, `.demo-step-label`
- **Add:** URL parameter support — `?task_id=...` loads directly into view mode
- **Update:** `<title>` from "ATE — Task Lifecycle Demo" to "ATE — Task Lifecycle"
- **Update:** Nav link text from "Task Lifecycle" to remove any "Demo" labeling
- **Keep:** Phase strip, two-column layout, bottom ticker, all other CSS

### 5. shared.js Changes

- **Remove:** `perturbEconomy()` function (lines 317-341)
- **Remove:** `startEconomyPerturbation` alias (line 430)
- **Keep:** All API client functions, SSE, ticker builders, utilities

## File Change Summary

| File | Action | Description |
|------|--------|-------------|
| `agents/src/base_agent/user_agent.py` | Create | UserAgent subclass of PlatformAgent |
| `services/ui/config.yaml` | Modify | Add user_agent config section |
| `services/ui/pyproject.toml` | Modify | Add base-agent path dependency |
| `services/ui/src/ui_service/config.py` | Modify | Add UserAgentSettings |
| `services/ui/src/ui_service/core/state.py` | Modify | Add user_agent field to AppState |
| `services/ui/src/ui_service/core/lifespan.py` | Modify | Instantiate UserAgent on startup |
| `services/ui/src/ui_service/routers/proxy.py` | Create | Proxy endpoints for task lifecycle |
| `services/ui/src/ui_service/services/proxy.py` | Create | Proxy business logic |
| `services/ui/src/ui_service/schemas.py` | Modify | Add proxy request/response models |
| `services/ui/src/ui_service/app.py` | Modify | Register proxy router |
| `data/web/task.html` | Modify | Remove demo controls, update title |
| `data/web/assets/task.js` | Rewrite | Data-driven view replacing scripted demo |
| `data/web/assets/shared.js` | Modify | Remove perturbEconomy backward-compat |

## Out of Scope

- **Test Agent**: Flagged for follow-up. Needed for integration tests where counterparty actions must be simulated.
- **File upload**: No file upload UI. Asset management remains agent-side.
- **Agent selection dropdown**: The UI always acts as the platform agent. No agent picker.
- **Bidding from UI**: The UI can accept bids (as poster) but cannot submit bids (as worker). Bids come from autonomous agents or the future test agent.
