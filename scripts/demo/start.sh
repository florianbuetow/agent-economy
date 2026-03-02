#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────
# Demo Start Script
# Starts all backend services, waits for health, and
# optionally starts the task feeder + math worker agents.
# ─────────────────────────────────────────────────────────
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
cd "$REPO_ROOT"

# ── Colors ──
C="\033[0;36m"  # cyan
G="\033[0;32m"  # green
R="\033[0;31m"  # red
Y="\033[0;33m"  # yellow
D="\033[0;34m"  # dim blue
N="\033[0m"     # reset

printf "\n${C}╔══════════════════════════════════════════╗${N}\n"
printf "${C}║    Agent Task Economy — Demo Launcher    ║${N}\n"
printf "${C}╚══════════════════════════════════════════╝${N}\n\n"

# ── Parse flags ──
START_AGENTS=false
FUND_AMOUNT=1000
SKIP_SERVICES=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --agents)     START_AGENTS=true; shift ;;
        --fund)       FUND_AMOUNT="$2"; shift 2 ;;
        --skip-services) SKIP_SERVICES=true; shift ;;
        -h|--help)
            printf "Usage: %s [OPTIONS]\n\n" "$0"
            printf "Options:\n"
            printf "  --agents          Also start task feeder + math worker agent\n"
            printf "  --fund <amount>   Fund the feeder agent (default: 1000)\n"
            printf "  --skip-services   Skip starting services (assume already running)\n"
            printf "  -h, --help        Show this help\n\n"
            exit 0
            ;;
        *) printf "${R}Unknown option: %s${N}\n" "$1"; exit 1 ;;
    esac
done

# ── Helper: wait for a service health endpoint ──
wait_for_health() {
    local name=$1 port=$2 max_attempts=30 attempt=0
    while [ $attempt -lt $max_attempts ]; do
        if curl -s --connect-timeout 1 "http://localhost:${port}/health" | grep -q '"ok"' 2>/dev/null; then
            printf "  ${G}✓${N} %s ready (port %s)\n" "$name" "$port"
            return 0
        fi
        attempt=$((attempt + 1))
        sleep 1
    done
    printf "  ${R}✗${N} %s failed to start on port %s\n" "$name" "$port"
    return 1
}

# ── Step 1: Stop any existing services ──
if [ "$SKIP_SERVICES" = false ]; then
    printf "${D}── Stopping existing services ──${N}\n"
    just stop-all 2>/dev/null || true
    printf "\n"

    # ── Step 2: Wipe stale data for clean demo ──
    printf "${D}── Wiping service databases for clean demo ──${N}\n"
    for svc in services/identity/ services/central-bank/ services/task-board/ services/reputation/ services/court/ services/db-gateway/; do
        rm -f "${svc}"data/*.db "${svc}"data/*.db-wal "${svc}"data/*.db-shm 2>/dev/null || true
    done
    printf "  ${G}✓${N} Databases wiped\n\n"

    # ── Step 3: Start all services ──
    printf "${D}── Starting services (tiered) ──${N}\n\n"

    printf "  Tier 1: Identity, Reputation, DB Gateway, UI\n"
    cd services/identity && uv run uvicorn identity_service.app:create_app --factory --host 127.0.0.1 --port 8001 > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    cd services/reputation && uv run uvicorn reputation_service.app:create_app --factory --host 127.0.0.1 --port 8004 > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    cd services/db-gateway && uv run uvicorn db_gateway_service.app:create_app --factory --host 127.0.0.1 --port 8007 > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    cd services/ui && uv run uvicorn ui_service.app:create_app --factory --host 127.0.0.1 --port 8008 > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    wait_for_health "Identity" 8001
    wait_for_health "Reputation" 8004
    wait_for_health "DB Gateway" 8007
    wait_for_health "UI" 8008
    printf "\n"

    printf "  Tier 2: Central Bank\n"
    cd services/central-bank && uv run uvicorn central_bank_service.app:create_app --factory --host 127.0.0.1 --port 8002 > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    wait_for_health "Central Bank" 8002
    printf "\n"

    printf "  Tier 3: Task Board\n"
    cd services/task-board && uv run uvicorn task_board_service.app:create_app --factory --host 127.0.0.1 --port 8003 > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    wait_for_health "Task Board" 8003
    printf "\n"

    printf "  Tier 4: Court\n"
    cd services/court && set -a && [ -f .env ] && . .env; set +a && uv run uvicorn court_service.app:create_app --factory --host 127.0.0.1 --port 8005 > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    wait_for_health "Court" 8005
    printf "\n"

    printf "${G}✓ All 7 services running${N}\n\n"
fi

# ── Step 4 (optional): Start agents ──
if [ "$START_AGENTS" = true ]; then
    printf "${D}── Starting agents ──${N}\n\n"

    printf "  Funding feeder agent with %s coins...\n" "$FUND_AMOUNT"
    just fund-feeder "$FUND_AMOUNT" > /dev/null 2>&1 && \
        printf "  ${G}✓${N} Feeder funded\n" || \
        printf "  ${Y}⚠${N} Feeder funding failed (may need services running first)\n"

    printf "  Starting task feeder...\n"
    cd agents && uv run python -m task_feeder > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    printf "  ${G}✓${N} Task feeder started (PID: $!)\n"

    printf "  Starting math worker (mathbot)...\n"
    cd agents && uv run python -m math_worker > /dev/null 2>&1 &
    cd "$REPO_ROOT"
    printf "  ${G}✓${N} Math worker started (PID: $!)\n"
    printf "\n"
fi

# ── Summary ──
printf "${C}╔══════════════════════════════════════════╗${N}\n"
printf "${C}║          Demo Environment Ready          ║${N}\n"
printf "${C}╠══════════════════════════════════════════╣${N}\n"
printf "${C}║${N}  Landing page:  http://localhost:8008     ${C}║${N}\n"
printf "${C}║${N}  Task demo:     http://localhost:8008/task.html ${C}║${N}\n"
printf "${C}╠══════════════════════════════════════════╣${N}\n"
printf "${C}║${N}  Stop all:      just stop-all             ${C}║${N}\n"
printf "${C}║${N}  Run browser:   scripts/demo/browser.py   ${C}║${N}\n"
printf "${C}╚══════════════════════════════════════════╝${N}\n\n"
