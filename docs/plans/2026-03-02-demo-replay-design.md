# Demo Replay System — Design

**Date:** 2026-03-02
**Status:** Approved

## Goal

Create a demo replay system that executes pre-defined scenarios against the live service stack, producing real-time events visible in the Observatory and UI via SSE. Two just targets: `just demo` (quick, ~25s) and `just demo-scale` (scaled economy, ~60s).

## Architecture

```
YAML Scenario File
       │
       ▼
  Replay Engine (Python, async)
       │
       ├─► Identity Service (8001)   — register agents
       ├─► Central Bank (8002)       — create accounts, fund via platform salary
       ├─► Task Board (8003)         — post tasks, bid, accept, submit, approve/dispute
       ├─► Reputation (8004)         — submit feedback
       └─► Court (8005)              — file claims (dispute path)
              │
              ▼
         DB Gateway (8007) ← all services write here
              │
              ▼
         SQLite (data/economy.db) → events table
              │
              ▼
         Observatory (8006) / UI (8008) ← SSE poll events table
```

## Components

### 1. Replay Engine — `tools/src/demo_replay/`

```
tools/src/demo_replay/
├── __init__.py
├── __main__.py          # CLI entry: uv run python -m demo_replay <scenario.yaml>
├── engine.py            # Load scenario, iterate steps, execute with delays
├── wallet.py            # Ed25519 key gen + JWS signing (extracted from agents/signing.py)
├── clients.py           # Async httpx wrappers for each service endpoint
└── actions.py           # Step handlers: register, fund, post_task, bid, accept, submit, approve, dispute
```

**Key decisions:**

- **Copy signing logic, don't import from agents.** The agents package has heavy deps (strands-agents, openai) we don't need. Extract the ~50 lines of Ed25519 + JWS code into `wallet.py`. The tools package already depends on `cryptography` is not present — we add it to `tools/pyproject.toml`.
- **Async httpx** for all API calls. Already a tools dependency.
- **Rich console** for terminal output — progress, step descriptions, timing. Already a tools dependency.
- **No LLM calls.** Court disputes in demo scenarios use pre-determined outcomes. If the Court service requires an LLM for ruling, we either: (a) skip the ruling step in the demo, or (b) ensure the Court has a configured LLM. The replay engine itself never calls an LLM.

### 2. Scenario Files — `tools/scenarios/`

YAML format. Each scenario defines agents, then a sequence of steps with optional per-step delays.

```yaml
name: "Quick Demo"
description: "Single task lifecycle: happy path + dispute"
default_delay: 2.0  # seconds between steps

agents:
  - handle: alice
    name: "Alice (Poster)"
  - handle: bob
    name: "Bob (Worker)"
  - handle: carol
    name: "Carol (Worker)"

steps:
  # --- Setup ---
  - action: register
    agent: alice

  - action: register
    agent: bob

  - action: register
    agent: carol

  - action: fund
    agent: alice
    amount: 5000

  - action: fund
    agent: bob
    amount: 1000

  - action: fund
    agent: carol
    amount: 1000

  # --- Happy path ---
  - action: post_task
    poster: alice
    title: "Implement login page"
    spec: "Build a responsive login form with email/password fields, validation, and error states. Must include forgot-password link."
    reward: 200
    delay: 3.0  # override default delay

  - action: bid
    bidder: bob
    amount: 180

  - action: bid
    bidder: carol
    amount: 190

  - action: accept_bid
    poster: alice
    bidder: bob

  - action: upload_asset
    worker: bob
    filename: "login.html"
    content: "<html>...</html>"

  - action: submit_deliverable
    worker: bob

  - action: approve
    poster: alice

  # --- Dispute path ---
  - action: post_task
    poster: alice
    title: "Design REST API"
    spec: "Design a RESTful API for a todo app with CRUD operations."
    reward: 150

  - action: bid
    bidder: carol
    amount: 140

  - action: accept_bid
    poster: alice
    bidder: carol

  - action: submit_deliverable
    worker: carol

  - action: dispute
    poster: alice
    reason: "Deliverable missing PATCH endpoint specification"
```

`scale.yaml` would have 10-15 agents, 5-8 tasks posted in waves, multiple concurrent lifecycles, and reputation feedback exchanges.

### 3. Step Execution Model

The engine processes steps sequentially:

1. Read step from scenario
2. Resolve agent handles to registered agent_ids (from in-memory registry)
3. Resolve task references (most recent task posted by that poster, or explicit task_ref)
4. Build signed payload (JWS with Ed25519)
5. Execute HTTP call against the service
6. Log result to console (Rich)
7. Sleep for `step.delay` or `scenario.default_delay`

**Task reference resolution:** Steps like `bid`, `accept_bid`, `submit_deliverable` need a `task_id`. The engine tracks the most recent task posted by each agent. If a scenario needs explicit task references, it can use a `task_ref: my_task_1` field on `post_task` and reference it from subsequent steps.

**Funding mechanism:** The `fund` action uses the platform agent to run a salary round targeting the specified agent. This mirrors how agents get funded in production.

### 4. Just Targets

Root `justfile` additions:

```just
# Run quick demo (3 agents, 1 task lifecycle + 1 dispute, ~25s)
demo:
    # ... starts services, wipes DB, runs quick.yaml

# Run scaled demo (10+ agents, multiple task waves, ~60s)
demo-scale:
    # ... starts services, wipes DB, runs scale.yaml
```

Each target:
1. Stops running services (`just stop-all`)
2. Wipes `data/economy.db` and service DBs
3. Starts all services (`just start-all`)
4. Waits for health checks
5. Runs: `cd tools && uv run python -m demo_replay scenarios/<name>.yaml`

### 5. Dependencies

Add to `tools/pyproject.toml`:
- `cryptography>=44.0.0` (Ed25519 signing)

Already present: `httpx`, `pyyaml`, `rich`

## What This Does NOT Include

- **No service startup/management inside the Python script** — that's the justfile's job
- **No parallel step execution** — sequential is predictable and the UI handles it fine
- **No LLM integration** — court rulings happen via the Court service's own LLM config
- **No persistent state between runs** — every `just demo` starts fresh

## Task Reference Design

For the quick scenario (one poster, sequential tasks), implicit "most recent task" tracking works. For the scale scenario with multiple concurrent posters, explicit task refs are needed:

```yaml
- action: post_task
  poster: alice
  ref: api_task          # <-- named reference
  title: "Design REST API"
  reward: 150

- action: bid
  task_ref: api_task     # <-- explicit reference
  bidder: bob
  amount: 140
```

The engine maintains a `refs: dict[str, str]` mapping ref names to task_ids.
