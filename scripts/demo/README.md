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

Starts all 8 services in dependency order, waits for health checks.

```bash
./scripts/demo/start.sh              # Services only (for scripted UI demo)
./scripts/demo/start.sh --agents     # Also start feeder + math worker
./scripts/demo/start.sh --skip-services  # Skip services (already running)
```

## Script 2: `browser.py` — Browser Automation

Opens Chromium and walks through the demo:

1. Landing page (hero, KPI strip, exchange board, leaderboard)
2. Task lifecycle — types a real problem from `data/math_tasks.jsonl` into the form
3. Clicks through all 12 steps (post, bid, contract, deliver, dispute, ruling, settle)

```bash
# Default: 3s between steps, auto-picked task
uv run --with playwright scripts/demo/browser.py

# Faster pace
uv run --with playwright scripts/demo/browser.py --step-delay 2

# Slower typing for dramatic effect
uv run --with playwright scripts/demo/browser.py --type-delay 50

# Skip landing, task demo only
uv run --with playwright scripts/demo/browser.py --no-landing

# Use a specific task from the database
uv run --with playwright scripts/demo/browser.py --task-index 42
```

## Stopping

```bash
just stop-all
```
