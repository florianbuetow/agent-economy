# Demo Scripts

Two scripts to run the 2-minute ATE demo.

## Quick Start

```bash
# Terminal 1: Start backend services
./scripts/demo/start.sh

# Terminal 2: Run browser automation
uv run --with playwright scripts/demo/browser.py
```

## First-Time Setup

Install Playwright browser binaries (one-time):

```bash
uv run --with playwright python -m playwright install chromium
```

## Script 1: `start.sh` — Backend Launcher

Starts all 7 services (identity, central-bank, task-board, reputation, court, db-gateway, ui) in dependency order, waits for health checks.

```bash
./scripts/demo/start.sh              # Services only (for scripted UI demo)
./scripts/demo/start.sh --agents     # Also start feeder + math worker
./scripts/demo/start.sh --skip-services  # Skip services (already running)
```

## Script 2: `browser.py` — Browser Automation

Opens Chromium and walks through the demo:

1. Landing page (hero, KPI strip, exchange board, leaderboard)
2. Task lifecycle — types a real problem from `data/math_tasks.jsonl` into the form
3. Clicks through all 11 steps (post, bid, accept, deliver, dispute, rebuttal, ruling, settle, feedback)

```bash
# Default: 3s between steps, auto-picked task
uv run --with playwright scripts/demo/browser.py

# Faster pace
uv run --with playwright scripts/demo/browser.py --step-delay 2

# Skip landing, task demo only
uv run --with playwright scripts/demo/browser.py --no-landing

# Headless (for CI), saving screenshots
uv run --with playwright scripts/demo/browser.py --headless --screenshots /tmp/demo-shots

# Point at a non-default UI host/port
uv run --with playwright scripts/demo/browser.py --base-url http://localhost:8008
```

## Stopping

```bash
just stop-all
```
