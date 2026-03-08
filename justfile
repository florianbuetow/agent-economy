# =============================================================================
# Justfile Rules (follow these when editing justfile):
#
# 1. Use printf (not echo) to print colors — some terminals won't render
#    colors with echo.
#
# 2. Always add an empty `@echo ""` line before and after each target's
#    command block.
#
# 3. Always add new targets to the help section and update it when targets
#    are added, modified or removed.
#
# 4. Target ordering in help (and in this file) matters:
#    - Setup targets first (init, setup, install, etc.)
#    - Start/stop/run targets next
#    - Code generation / data tooling targets next
#    - Checks, linting, and tests next (ordered fastest to slowest)
#    Group related targets together and separate groups with an empty
#    `@echo ""` line in the help output.
#
# 5. Composite targets (e.g. ci) that call multiple sub-targets must fail
#    fast: exit 1 on the first error. Never skip over errors or warnings.
#    Use `set -e` or `&&` chaining to ensure immediate abort with the
#    appropriate error message.
#
# 6. Every target must end with a clear short status message:
#    - On success: green (\033[32m) message confirming completion.
#      E.g. printf "\033[32m✓ init completed successfully\033[0m\n"
#    - On failure: red (\033[31m) message indicating what failed, then exit 1.
#      E.g. printf "\033[31m✗ ci failed: tests exited with errors\033[0m\n"
# =============================================================================

# Default recipe: show help
_default:
    @just help

# Show available commands
[private]
help:
    @clear
    @echo ""
    @printf "\033[0;34m=== Agent Task Economy ===\033[0m\n"
    @echo ""
    @printf "\033[1;33mSetup\033[0m\n"
    @printf "  \033[0;37mjust check            \033[0;34m Check if all required tools are installed\033[0m\n"
    @printf "  \033[0;37mjust init             \033[0;34m Initialize project (git hooks, tooling)\033[0m\n"
    @printf "  \033[0;37mjust init-all         \033[0;34m Initialize all service environments\033[0m\n"
    @printf "  \033[0;37mjust destroy-all      \033[0;34m Destroy all virtual environments\033[0m\n"
    @printf "  \033[0;37mjust setup-guard      \033[0;34m Set up guard file integrity collections\033[0m\n"
    @echo ""
    @printf "\033[1;33mLocal Development\033[0m\n"
    @printf "  \033[0;37mjust start-all        \033[0;34m Start all services in background\033[0m\n"
    @printf "  \033[0;37mjust start-identity   \033[0;34m Start identity service locally\033[0m\n"
    @printf "  \033[0;37mjust start-bank       \033[0;34m Start central bank service locally\033[0m\n"
    @printf "  \033[0;37mjust start-taskboard  \033[0;34m Start task board service locally\033[0m\n"
    @printf "  \033[0;37mjust start-reputation \033[0;34m Start reputation service locally\033[0m\n"
    @printf "  \033[0;37mjust start-court      \033[0;34m Start court service locally\033[0m\n"
    @printf "  \033[0;37mjust start-db-gateway \033[0;34m Start db-gateway service locally\033[0m\n"
    @printf "  \033[0;37mjust start-ui         \033[0;34m Start UI service locally\033[0m\n"
    @printf "  \033[0;37mjust stop-all         \033[0;34m Stop all locally running services\033[0m\n"
    @printf "  \033[0;37mjust stop-identity    \033[0;34m Stop identity service\033[0m\n"
    @printf "  \033[0;37mjust stop-bank        \033[0;34m Stop central bank service\033[0m\n"
    @printf "  \033[0;37mjust stop-taskboard   \033[0;34m Stop task board service\033[0m\n"
    @printf "  \033[0;37mjust stop-reputation  \033[0;34m Stop reputation service\033[0m\n"
    @printf "  \033[0;37mjust stop-court       \033[0;34m Stop court service\033[0m\n"
    @printf "  \033[0;37mjust stop-db-gateway  \033[0;34m Stop db-gateway service\033[0m\n"
    @printf "  \033[0;37mjust stop-ui          \033[0;34m Stop UI service\033[0m\n"
    @printf "  \033[0;37mjust start-feeder     \033[0;34m Start task feeder (posts math tasks)\033[0m\n"
    @printf "  \033[0;37mjust stop-feeder      \033[0;34m Stop task feeder\033[0m\n"
    @printf "  \033[0;37mjust start-mathbot    \033[0;34m Start math worker agent (requires services + LM Studio)\033[0m\n"
    @printf "  \033[0;37mjust stop-mathbot     \033[0;34m Stop math worker agent\033[0m\n"
    @printf "  \033[0;37mjust fund-feeder <amount>\033[0;34m Fund the feeder agent with initial coins\033[0m\n"
    @printf "  \033[0;37mjust status           \033[0;34m Check health status of all services\033[0m\n"
    @printf "  \033[0;37mjust watch            \033[0;34m Continuously watch service status (refreshes every 5s)\033[0m\n"
    @printf "  \033[0;37mjust logs             \033[0;34m Tail all service logs (color-coded)\033[0m\n"
    @echo ""
    @printf "\033[1;33mDocker\033[0m\n"
    @printf "  \033[0;37mjust docker-up        \033[0;34m Start all services\033[0m\n"
    @printf "  \033[0;37mjust docker-up-dev    \033[0;34m Start all services with hot reload\033[0m\n"
    @printf "  \033[0;37mjust docker-down      \033[0;34m Stop all services\033[0m\n"
    @printf "  \033[0;37mjust docker-logs      \033[0;34m View logs (optionally: just docker-logs <service>)\033[0m\n"
    @printf "  \033[0;37mjust docker-build     \033[0;34m Rebuild all Docker images from scratch\033[0m\n"
    @echo ""
    @printf "\033[1;33mTask Generation\033[0m\n"
    @printf "  \033[0;37mjust generate-tasks    \033[0;34m Generate math tasks to data/math_tasks.jsonl\033[0m\n"
    @echo ""
    @printf "\033[1;33mDemo\033[0m\n"
    @printf "  \033[0;37mjust demo             \033[0;34m Run quick demo (3 agents, ~25s)\033[0m\n"
    @printf "  \033[0;37mjust demo-scale       \033[0;34m Run scaled demo (10 agents, ~60s)\033[0m\n"
    @echo ""
    @printf "\033[1;33mCI & Code Quality\033[0m\n"
    @printf "  \033[0;37mjust test-all         \033[0;34m Run tests for all services\033[0m\n"
    @printf "  \033[0;37mjust test-architecture\033[0;34m Run architecture tests for all services\033[0m\n"
    @printf "  \033[0;37mjust test-project-structure\033[0;34m Verify all service justfiles are identical\033[0m\n"
    @printf "  \033[0;37mjust test <service>   \033[0;34m Run tests for a specific service\033[0m\n"
    @printf "  \033[0;37mjust ci               \033[0;34m Run ALL CI checks (services, agents, integration, e2e)\033[0m\n"
    @printf "  \033[0;37mjust ci-quiet         \033[0;34m Run ALL CI checks quietly\033[0m\n"
    @printf "  \033[0;37mjust ci-service <svc> \033[0;34m Run CI checks for a specific service\033[0m\n"
    @printf "  \033[0;37mjust ci-quiet-hook    \033[0;34m CI hook for Claude Code (blocks git commit if CI fails)\033[0m\n"
    @printf "  \033[0;37mjust format-all       \033[0;34m Auto-format all services\033[0m\n"
    @printf "  \033[0;37mjust test-integration \033[0;34m Run cross-service integration tests\033[0m\n"
    @printf "  \033[0;37mjust test-e2e         \033[0;34m Run e2e tests (starts/stops services automatically)\033[0m\n"
    @printf "  \033[0;37mjust stats            \033[0;34m Show lines of code across the project\033[0m\n"
    @echo ""

# --- Setup ---

# Check if all required tools are installed
check:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Checking Required Tools ===\033[0m\n"
    printf "\n"
    missing=0

    check_tool() {
        local name=$1
        local cmd=$2
        local version_flag=${3:---version}
        if command -v "$cmd" >/dev/null 2>&1; then
            version=$("$cmd" $version_flag 2>&1 | head -1)
            printf "\033[0;32m✓ %s\033[0m - %s\n" "$name" "$version"
        else
            printf "\033[0;31m✗ %s\033[0m - not found\n" "$name"
            missing=$((missing + 1))
        fi
    }

    check_tool "uv"     uv     "--version"
    check_tool "python"  python3 "--version"
    check_tool "docker"  docker  "--version"
    check_tool "curl"    curl    "--version"
    check_tool "jq"      jq      "--version"
    check_tool "lsof"    lsof    "-v"
    check_tool "bd"      bd      "--version"
    check_tool "just"    just    "--version"

    printf "\n"
    if [ "$missing" -gt 0 ]; then
        printf "\033[0;31m✗ %d tool(s) missing\033[0m\n" "$missing"
        exit 1
    else
        printf "\033[0;32m✓ All required tools are installed\033[0m\n"
    fi
    printf "\n"

# Set up guard file integrity collections
setup-guard:
    @echo ""
    @printf "\033[0;34m=== Setting Up Guard Collections ===\033[0m\n"
    bash scripts/setup_guard.sh
    @printf "\033[0;32m✓ Guard collections configured\033[0m\n"
    @echo ""

# --- Local Development ---

# Initialize project (git hooks, tooling)
init:
    @echo ""
    bash scripts/init-hooks.sh
    @echo ""

# Initialize all service environments
init-all:
    @echo ""
    @printf "\033[0;34m=== Initializing All Services ===\033[0m\n"
    cd services/identity && just init
    cd services/central-bank && just init
    cd services/task-board && just init
    cd services/reputation && just init
    cd services/court && just init
    cd services/db-gateway && just init
    cd services/ui && just init
    cd agents && just init
    cd tools && just init
    @printf "\033[0;32m✓ All services initialized\033[0m\n"
    @echo ""

# Start identity service locally
start-identity:
    @echo ""
    cd services/identity && just run
    @echo ""

# Start central bank service locally
start-bank:
    @echo ""
    cd services/central-bank && just run
    @echo ""

# Start task board service locally
start-taskboard:
    @echo ""
    cd services/task-board && just run
    @echo ""

# Start reputation service locally
start-reputation:
    @echo ""
    cd services/reputation && just run
    @echo ""

# Start court service locally
start-court:
    @echo ""
    cd services/court && just run
    @echo ""

# Start db-gateway service locally
start-db-gateway:
    @echo ""
    cd services/db-gateway && just run
    @echo ""

# Start UI service locally
start-ui:
    @echo ""
    cd services/ui && just run
    @echo ""

# Start all services in background (respects dependency order)
start-all:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Starting All Services (Background) ===\033[0m\n"
    printf "\n"

    wait_for_health() {
        local name=$1
        local port=$2
        local max_attempts=60
        local attempt=0
        while [ $attempt -lt $max_attempts ]; do
            if curl -s --connect-timeout 1 "http://localhost:${port}/health" | grep -q '"ok"' 2>/dev/null; then
                printf "\033[0;32m✓ %s ready (port %s)\033[0m\n" "$name" "$port"
                return 0
            fi
            attempt=$((attempt + 1))
            sleep 0.3
        done
        printf "\033[0;31m✗ %s failed to start on port %s\033[0m\n" "$name" "$port"
        return 1
    }

    # Tier 1: DB Gateway first (creates economy.db needed by UI)
    printf "Starting tier 1 (DB Gateway)...\n"
    cd services/db-gateway && uv run uvicorn db_gateway_service.app:create_app --factory --host 127.0.0.1 --port 8007 &
    wait_for_health "DB Gateway" 8007

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

    printf "\n"
    printf "\033[0;32m✓ All services started\033[0m\n"
    printf "\033[0;32m  UI Service: http://localhost:8008\033[0m\n"
    printf "\n"

# Stop identity service
stop-identity:
    @echo ""
    cd services/identity && just kill
    @echo ""

# Stop central bank service
stop-bank:
    @echo ""
    cd services/central-bank && just kill
    @echo ""

# Stop task board service
stop-taskboard:
    @echo ""
    cd services/task-board && just kill
    @echo ""

# Stop reputation service
stop-reputation:
    @echo ""
    cd services/reputation && just kill
    @echo ""

# Stop court service
stop-court:
    @echo ""
    cd services/court && just kill
    @echo ""

# Stop db-gateway service
stop-db-gateway:
    @echo ""
    cd services/db-gateway && just kill
    @echo ""

# Stop UI service
stop-ui:
    @echo ""
    cd services/ui && just kill
    @echo ""

# Stop all locally running services
stop-all:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Stopping All Services (Local) ===\033[0m\n"
    printf "\n"

    failed=0
    for svc in identity central-bank task-board reputation court db-gateway ui; do
        cd "services/$svc" && just kill || failed=$((failed + 1))
        cd - > /dev/null
    done

    # Also stop agents if running
    pkill -f "python -m task_feeder" 2>/dev/null && printf "Task feeder stopped\n" || true
    pkill -f "python -m math_worker" 2>/dev/null && printf "Math worker stopped\n" || true

    if [ "$failed" -gt 0 ]; then
        printf "\033[0;33m⚠ %d service(s) could not be stopped\033[0m\n" "$failed"
    else
        printf "\033[0;32m✓ All services stopped\033[0m\n"
    fi
    printf "\n"

# Start task feeder (posts math tasks onto the board)
start-feeder:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Starting Task Feeder ===\033[0m\n"
    printf "\n"
    cd agents && uv run python -m task_feeder &
    printf "Task feeder starting in background (PID: $!)\n"
    printf "\n"

# Stop task feeder
stop-feeder:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Stopping Task Feeder ===\033[0m\n"
    pkill -f "python -m task_feeder" 2>/dev/null && \
        printf "\033[0;32m✓ Task feeder stopped\033[0m\n" || \
        printf "\033[0;33m⚠ Task feeder not running\033[0m\n"
    printf "\n"

# Start math worker agent (requires running services + LM Studio)
start-mathbot:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Starting Math Worker Agent ===\033[0m\n"
    printf "\n"
    cd agents && uv run python -m math_worker &
    printf "Math worker agent starting in background (PID: $!)\n"
    printf "\n"

# Stop math worker agent
stop-mathbot:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Stopping Math Worker Agent ===\033[0m\n"
    pkill -f "python -m math_worker" 2>/dev/null && \
        printf "\033[0;32m✓ Math worker agent stopped\033[0m\n" || \
        printf "\033[0;33m⚠ Math worker agent not running\033[0m\n"
    printf "\n"

# Fund the feeder agent with initial coins
fund-feeder amount:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Funding Feeder Agent ===\033[0m\n"
    printf "\n"
    cd agents && uv run python -m fund_feeder_cli {{amount}}
    exit_code=$?
    printf "\n"
    if [ $exit_code -eq 0 ]; then
        printf "\033[0;32m✓ Feeder agent funded successfully\033[0m\n"
    else
        printf "\033[0;31m✗ Failed to fund feeder agent\033[0m\n"
        exit 1
    fi
    printf "\n"

# Check health status of all services
status:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Service Status ===\033[0m\n"
    printf "\n"

    check_service() {
        local name=$1
        local port=$2
        local health_response

        if health_response=$(curl -s --connect-timeout 2 "http://localhost:${port}/health" 2>/dev/null); then
            status=$(echo "$health_response" | jq -r '.status // empty' 2>/dev/null)
            if [ "$status" = "ok" ]; then
                uptime=$(echo "$health_response" | jq -r '.uptime_seconds // empty' 2>/dev/null)
                agents=$(echo "$health_response" | jq -r '.registered_agents // empty' 2>/dev/null)
                printf "\033[0;32m✓ %s\033[0m (port %s) - ok" "$name" "$port"
                [ -n "$uptime" ] && printf " (uptime: %ss)" "$uptime"
                [ -n "$agents" ] && printf " (agents: %s)" "$agents"
                printf "\n"
            else
                printf "\033[0;33m⚠ %s\033[0m (port %s) - %s\n" "$name" "$port" "${status:-unknown}"
            fi
        else
            printf "\033[0;31m✗ %s\033[0m (port %s) - not responding\n" "$name" "$port"
        fi
    }

    check_service "Identity"     8001
    check_service "Central Bank" 8002
    check_service "Task Board"   8003
    check_service "Reputation"   8004
    check_service "Court"        8005
    check_service "DB Gateway"   8007
    check_service "UI"           8008

    # Check LM Studio if any court judge is configured to use it
    court_config="services/court/config.yaml"
    if [ -f "$court_config" ]; then
        api_base=$(grep -A5 'api_key_env:.*LMSTUDIO' "$court_config" | grep -B5 'api_base:' | grep 'api_base:' | head -1 | awk '{print $2}' | tr -d '"')
        if [ -z "$api_base" ]; then
            # Try the other order: api_base before api_key_env
            api_base=$(grep -B5 'api_key_env:.*LMSTUDIO' "$court_config" | grep 'api_base:' | head -1 | awk '{print $2}' | tr -d '"')
        fi
        if [ -n "$api_base" ]; then
            printf "\n"
            if curl -s --connect-timeout 2 "${api_base}/models" >/dev/null 2>&1; then
                printf "\033[0;32m✓ LM Studio\033[0m (%s) - online\n" "$api_base"
            else
                printf "\033[0;31m✗ LM Studio\033[0m (%s) - not responding\n" "$api_base"
            fi
        fi
    fi

    printf "\n"

# Continuously watch service status (refreshes every 5s)
watch:
    #!/usr/bin/env bash
    while true; do
        clear
        just status
        sleep 5
    done

# Tail all service logs (color-coded, today by default)
logs date="":
    #!/usr/bin/env bash
    printf "\n"
    if [ -n "{{date}}" ]; then
        python3 scripts/tail_logs.py --date "{{date}}"
    else
        python3 scripts/tail_logs.py
    fi

# Destroy all virtual environments
destroy-all: stop-all
    @echo ""
    @printf "\033[0;34m=== Destroying All Virtual Environments ===\033[0m\n"
    cd services/identity && just destroy
    cd services/central-bank && just destroy
    cd services/task-board && just destroy
    cd services/reputation && just destroy
    cd services/court && just destroy
    cd services/db-gateway && just destroy
    cd services/ui && just destroy
    cd agents && just destroy
    cd tools && just destroy
    @printf "\033[0;32m✓ All virtual environments removed\033[0m\n"
    @echo ""

# --- Task Generation ---

# Generate math tasks (appends 10,000 tasks to data/math_tasks.jsonl)
generate-tasks:
    @echo ""
    cd tools && uv run python -m math_task_factory --total 10000
    @printf "\033[0;32m✓ Tasks appended to data/math_tasks.jsonl\033[0m\n"
    @echo ""

# --- Demo ---

# Run quick demo (3 agents, 1 task lifecycle + 1 dispute, ~25s)
demo:
    #!/usr/bin/env bash
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Quick Demo ===\033[0m\n"
    printf "\n"

    # Check if any services are already running and stop them
    ports=(8001 8002 8003 8004 8005 8007 8008)
    running=0
    for port in "${ports[@]}"; do
        if lsof -ti :"$port" -sTCP:LISTEN >/dev/null 2>&1; then
            running=1
            break
        fi
    done
    if [ "$running" -eq 1 ]; then
        printf "Stopping running services...\n"
        just stop-all
    fi

    printf "Wiping databases...\n"
    rm -f data/economy.db data/economy.db-wal data/economy.db-shm
    for svc in services/*/; do
        rm -f "$svc"data/*.db "$svc"data/*.db-wal "$svc"data/*.db-shm
    done

    printf "Starting services...\n"
    just start-all

    # Open UI in browser so user can watch the replay live
    printf "Opening UI in browser...\n"
    if command -v open >/dev/null 2>&1; then
        open "http://localhost:8008"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "http://localhost:8008"
    fi
    sleep 1

    printf "\n"
    printf "\033[0;34m--- Running quick scenario ---\033[0m\n"
    printf "\n"
    cd tools && uv run python -m demo_replay scenarios/quick.yaml
    exit_code=$?
    cd "$root"

    printf "\n"
    if [ $exit_code -eq 0 ]; then
        printf "\033[0;32m✓ Demo complete — UI at http://localhost:8008\033[0m\n"
    else
        printf "\033[0;31m✗ Demo failed (exit code: %d)\033[0m\n" "$exit_code"
    fi
    printf "\n"
    printf "\033[0;33mPress Enter to stop all services...\033[0m"
    read -r
    just stop-all
    printf "\n"

# Run scaled demo (10 agents, multiple task waves, ~60s)
demo-scale:
    #!/usr/bin/env bash
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Scaled Economy Demo ===\033[0m\n"
    printf "\n"

    # Check if any services are already running and stop them
    ports=(8001 8002 8003 8004 8005 8007 8008)
    running=0
    for port in "${ports[@]}"; do
        if lsof -ti :"$port" -sTCP:LISTEN >/dev/null 2>&1; then
            running=1
            break
        fi
    done
    if [ "$running" -eq 1 ]; then
        printf "Stopping running services...\n"
        just stop-all
    fi

    printf "Wiping databases...\n"
    rm -f data/economy.db data/economy.db-wal data/economy.db-shm
    for svc in services/*/; do
        rm -f "$svc"data/*.db "$svc"data/*.db-wal "$svc"data/*.db-shm
    done

    printf "Starting services...\n"
    just start-all

    # Open UI in browser so user can watch the replay live
    printf "Opening UI in browser...\n"
    if command -v open >/dev/null 2>&1; then
        open "http://localhost:8008"
    elif command -v xdg-open >/dev/null 2>&1; then
        xdg-open "http://localhost:8008"
    fi
    sleep 1

    printf "\n"
    printf "\033[0;34m--- Running scale scenario ---\033[0m\n"
    printf "\n"
    cd tools && uv run python -m demo_replay scenarios/scale.yaml
    exit_code=$?
    cd "$root"

    printf "\n"
    if [ $exit_code -eq 0 ]; then
        printf "\033[0;32m✓ Demo complete — UI at http://localhost:8008\033[0m\n"
    else
        printf "\033[0;31m✗ Demo failed (exit code: %d)\033[0m\n" "$exit_code"
    fi
    printf "\n"
    printf "\033[0;33mPress Enter to stop all services...\033[0m"
    read -r
    just stop-all
    printf "\n"

# --- Docker ---

# Start all services with Docker Compose
docker-up:
    @echo ""
    @printf "\033[0;34m=== Starting All Services (Docker) ===\033[0m\n"
    docker compose up -d
    @printf "\033[0;32m✓ Services started\033[0m\n"
    @echo ""

# Start all services in development mode (with hot reload)
docker-up-dev:
    @echo ""
    @printf "\033[0;34m=== Starting All Services (Docker Dev Mode) ===\033[0m\n"
    docker compose -f docker-compose.yml -f docker-compose.dev.yml up
    @echo ""

# Stop all services
docker-down:
    @echo ""
    @printf "\033[0;34m=== Stopping All Services ===\033[0m\n"
    docker compose down
    @printf "\033[0;32m✓ Services stopped\033[0m\n"
    @echo ""

# View Docker logs (optionally for a specific service)
docker-logs service="":
    @echo ""
    docker compose logs -f {{service}}
    @echo ""

# Build all Docker images (destroys existing images first)
docker-build:
    @echo ""
    @printf "\033[0;34m=== Destroying Existing Docker Images ===\033[0m\n"
    docker compose down --rmi all --volumes 2>/dev/null || true
    @printf "\033[0;34m=== Building All Docker Images ===\033[0m\n"
    docker compose build
    @printf "\033[0;32m✓ Build complete\033[0m\n"
    @echo ""

# --- CI & Code Quality ---

# Run architecture tests for all services
test-architecture:
    @echo ""
    @printf "\033[0;34m=== Running Architecture Tests ===\033[0m\n"
    cd services/identity && just test-architecture
    cd services/central-bank && just test-architecture
    cd services/task-board && just test-architecture
    cd services/reputation && just test-architecture
    cd services/court && just test-architecture
    cd services/db-gateway && just test-architecture
    cd services/ui && just test-architecture
    @printf "\033[0;32m✓ All architecture tests passed\033[0m\n"
    @echo ""

# Verify all service justfiles are byte-for-byte identical
test-project-structure:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Checking Project Structure ===\033[0m\n"
    printf "\n"
    reference="services/identity/justfile"
    if [ ! -f "$reference" ]; then
        printf "\033[0;31m✗ Reference justfile not found: %s\033[0m\n" "$reference"
        exit 1
    fi
    failed=0
    for svc in central-bank court db-gateway reputation task-board ui; do
        target="services/$svc/justfile"
        if [ ! -f "$target" ]; then
            printf "\033[0;31m✗ Missing justfile: %s\033[0m\n" "$target"
            failed=1
        elif ! diff -q "$reference" "$target" > /dev/null 2>&1; then
            printf "\033[0;31m✗ Justfile differs: %s\033[0m\n" "$target"
            diff --color "$reference" "$target" | head -20
            failed=1
        else
            printf "\033[0;32m✓ %s\033[0m\n" "$target"
        fi
    done
    if [ "$failed" -eq 1 ]; then
        printf "\n\033[0;31m✗ Service justfiles are not identical\033[0m\n"
        printf "  All service justfiles must be byte-for-byte identical.\n"
        printf "  They derive SERVICE_NAME, PORT, and DISPLAY_NAME from their directory and config.yaml.\n"
        exit 1
    fi
    printf "\n\033[0;32m✓ All service justfiles are identical\033[0m\n"
    printf "\n"

# Run tests for all services
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

# Run tests for a specific service
test service:
    @echo ""
    cd services/{{service}} && just test
    @echo ""

# Run CI checks for a specific service
ci-service service:
    @echo ""
    cd services/{{service}} && just ci
    @echo ""

# Run ALL CI checks: services, agents, cross-service integration tests, e2e tests
ci:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running Full CI ===\033[0m\n"
    printf "\n"

    # Phase 0: Project structure checks
    printf "\033[0;34m--- Phase 0: Project structure ---\033[0m\n"
    cd "$root" && just test-project-structure

    # Phase 1: Per-service CI (format, lint, types, security, deps, spell, semgrep, audit, tests, pyright)
    printf "\033[0;34m--- Phase 1: Service CI ---\033[0m\n"
    services=(identity central-bank task-board reputation court db-gateway ui)
    for svc in "${services[@]}"; do
        cd "$root/services/$svc" && just ci
    done

    # Phase 2: Agents CI (format, lint, types, security, spell, unit tests)
    printf "\033[0;34m--- Phase 2: Agents CI ---\033[0m\n"
    cd "$root/agents" && just ci

    # Phase 3: Cross-service integration tests (DB Gateway writes, offline gateway)
    printf "\033[0;34m--- Phase 3: Cross-service integration tests ---\033[0m\n"
    cd "$root"
    PYTHONPATH="$root/tests" uv run --directory "$root/services/db-gateway" \
        pytest "$root/tests/integration/" -v --tb=short

    # Phase 4: E2E tests (restarts services, runs full lifecycle tests)
    printf "\033[0;34m--- Phase 4: E2E tests ---\033[0m\n"
    cd "$root"
    just test-e2e

    printf "\n"
    printf "\033[0;32m✓ Full CI passed (services + agents + integration + e2e)\033[0m\n"
    printf "\n"

# Run ALL CI checks quietly
ci-quiet:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running Full CI (Quiet Mode) ===\033[0m\n"

    # Phase 0: Project structure checks
    printf "\033[0;34m--- Phase 0: Project structure ---\033[0m\n"
    cd "$root" && just test-project-structure

    # Phase 1: Per-service CI
    printf "\033[0;34m--- Phase 1: Service CI ---\033[0m\n"
    services=(identity central-bank task-board reputation court db-gateway ui)
    for svc in "${services[@]}"; do
        printf "Checking %s...\n" "$svc"
        cd "$root/services/$svc" && just ci-quiet
    done

    # Phase 2: Agents CI
    printf "\033[0;34m--- Phase 2: Agents CI ---\033[0m\n"
    cd "$root/agents" && just ci-quiet

    # Phase 3: Cross-service integration tests
    printf "\033[0;34m--- Phase 3: Cross-service integration tests ---\033[0m\n"
    cd "$root"
    PYTHONPATH="$root/tests" uv run --directory "$root/services/db-gateway" \
        pytest "$root/tests/integration/" -v --tb=short

    # Phase 4: E2E tests
    printf "\033[0;34m--- Phase 4: E2E tests ---\033[0m\n"
    cd "$root"
    just test-e2e

    printf "\n"
    printf "\033[0;32m✓ Full CI passed (services + agents + integration + e2e)\033[0m\n"
    printf "\n"

# CI hook for Claude Code — blocks git commit if CI fails
ci-quiet-hook:
    @bash scripts/ci-quiet-hook.sh

# Run cross-service integration tests (DB Gateway writes, offline gateway)
test-integration:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running Cross-Service Integration Tests ===\033[0m\n"
    printf "\n"
    PYTHONPATH="$root/tests" uv run --directory "$root/services/db-gateway" \
        pytest "$root/tests/integration/" -v --tb=short
    printf "\n"
    printf "\033[0;32m✓ Cross-service integration tests passed\033[0m\n"
    printf "\n"

# Run e2e tests (restarts all services with clean data, then runs tests)
test-e2e:
    #!/usr/bin/env bash
    set -uo pipefail
    printf "\n"
    printf "\033[0;34m=== Running E2E Tests ===\033[0m\n"
    printf "\n"

    root="$(pwd)"

    cleanup() {
        printf "\n"
        printf "\033[0;34m--- Stopping all services ---\033[0m\n"
        cd "$root" && just stop-all
    }
    trap cleanup EXIT

    printf "\033[0;34m--- Stopping all services ---\033[0m\n"
    just stop-all

    printf "\033[0;34m--- Wiping service databases ---\033[0m\n"
    for svc in services/*/; do
        rm -f "$svc"data/*.db "$svc"data/*.db-wal "$svc"data/*.db-shm
    done
    printf "\033[0;32m✓ Service databases wiped\033[0m\n"

    printf "\033[0;34m--- Starting all services ---\033[0m\n"
    just start-all

    printf "\033[0;34m--- Running e2e tests ---\033[0m\n"
    test_exit=0
    cd agents && just test-e2e || test_exit=$?

    printf "\n"
    if [ "$test_exit" -eq 0 ]; then
        printf "\033[0;32m✓ E2E tests passed\033[0m\n"
    else
        printf "\033[0;31m✗ E2E tests failed (exit code: %d)\033[0m\n" "$test_exit"
    fi
    printf "\n"
    exit "$test_exit"

# Show lines of code across the project
stats:
    @bash scripts/stats.sh

# Format all services
format-all:
    #!/usr/bin/env bash
    echo ""
    for dir in services/*/; do
        name=$(basename "$dir")
        if [ -f "$dir/justfile" ]; then
            cd "$dir" && just code-format && cd - > /dev/null
        fi
    done
    echo ""
