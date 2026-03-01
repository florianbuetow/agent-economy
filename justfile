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
    @printf "  \033[0;37mjust start-observatory\033[0;34m Start observatory service locally\033[0m\n"
    @printf "  \033[0;37mjust stop-all         \033[0;34m Stop all locally running services\033[0m\n"
    @printf "  \033[0;37mjust stop-identity    \033[0;34m Stop identity service\033[0m\n"
    @printf "  \033[0;37mjust stop-bank        \033[0;34m Stop central bank service\033[0m\n"
    @printf "  \033[0;37mjust stop-taskboard   \033[0;34m Stop task board service\033[0m\n"
    @printf "  \033[0;37mjust stop-reputation  \033[0;34m Stop reputation service\033[0m\n"
    @printf "  \033[0;37mjust stop-court       \033[0;34m Stop court service\033[0m\n"
    @printf "  \033[0;37mjust stop-observatory \033[0;34m Stop observatory service\033[0m\n"
    @printf "  \033[0;37mjust status           \033[0;34m Check health status of all services\033[0m\n"
    @echo ""
    @printf "\033[1;33mDocker\033[0m\n"
    @printf "  \033[0;37mjust docker-up        \033[0;34m Start all services\033[0m\n"
    @printf "  \033[0;37mjust docker-up-dev    \033[0;34m Start all services with hot reload\033[0m\n"
    @printf "  \033[0;37mjust docker-down      \033[0;34m Stop all services\033[0m\n"
    @printf "  \033[0;37mjust docker-logs      \033[0;34m View logs (optionally: just docker-logs <service>)\033[0m\n"
    @printf "  \033[0;37mjust docker-build     \033[0;34m Rebuild all Docker images from scratch\033[0m\n"
    @echo ""
    @printf "\033[1;33mCI & Code Quality\033[0m\n"
    @printf "  \033[0;37mjust test-all         \033[0;34m Run tests for all services\033[0m\n"
    @printf "  \033[0;37mjust test <service>   \033[0;34m Run tests for a specific service\033[0m\n"
    @printf "  \033[0;37mjust ci <service>     \033[0;34m Run CI checks for a specific service\033[0m\n"
    @printf "  \033[0;37mjust ci-all           \033[0;34m Run CI checks for all services (verbose)\033[0m\n"
    @printf "  \033[0;37mjust ci-all-quiet     \033[0;34m Run CI checks for all services (quiet)\033[0m\n"
    @printf "  \033[0;37mjust format-all       \033[0;34m Auto-format all services\033[0m\n"
    @printf "  \033[0;37mjust test-e2e   \033[0;34m Run e2e tests (requires running services)\033[0m\n"
    @printf "  \033[0;37mjust stats            \033[0;34m Show Python lines of code per service\033[0m\n"
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
    cd services/observatory && just init
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

# Start observatory service locally
start-observatory:
    @echo ""
    cd services/observatory && just run
    @echo ""

# Start all services in background
start-all:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Starting All Services (Background) ===\033[0m\n"
    printf "\n"

    cd services/identity && uv run uvicorn identity_service.app:create_app --factory --host 0.0.0.0 --port 8001 &
    cd services/central-bank && uv run uvicorn central_bank_service.app:create_app --factory --host 0.0.0.0 --port 8002 &
    cd services/task-board && uv run uvicorn task_board_service.app:create_app --factory --host 0.0.0.0 --port 8003 &
    cd services/reputation && uv run uvicorn reputation_service.app:create_app --factory --host 0.0.0.0 --port 8004 &
    cd services/court && uv run uvicorn court_service.app:create_app --factory --host 0.0.0.0 --port 8005 &
    cd services/observatory && uv run uvicorn observatory_service.app:create_app --factory --host 0.0.0.0 --port 8006 &

    printf "Services starting in background...\n"

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

# Stop observatory service
stop-observatory:
    @echo ""
    cd services/observatory && just kill
    @echo ""

# Stop all locally running services
stop-all:
    @echo ""
    @printf "\033[0;34m=== Stopping All Services (Local) ===\033[0m\n"
    cd services/identity && just kill
    cd services/central-bank && just kill
    cd services/task-board && just kill
    cd services/reputation && just kill
    cd services/court && just kill
    cd services/observatory && just kill
    @printf "\033[0;32m✓ All services stopped\033[0m\n"
    @echo ""

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
    check_service "Observatory"  8006

    printf "\n"

# Destroy all virtual environments
destroy-all:
    @echo ""
    @printf "\033[0;34m=== Destroying All Virtual Environments ===\033[0m\n"
    cd services/identity && just destroy
    cd services/central-bank && just destroy
    cd services/task-board && just destroy
    cd services/reputation && just destroy
    cd services/court && just destroy
    cd services/observatory && just destroy
    cd tools && just destroy
    @printf "\033[0;32m✓ All virtual environments removed\033[0m\n"
    @echo ""

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

# Run tests for all services
test-all:
    @echo ""
    @printf "\033[0;34m=== Running All Tests ===\033[0m\n"
    cd services/identity && just test
    cd services/central-bank && just test
    cd services/task-board && just test
    cd services/reputation && just test
    cd services/court && just test
    cd services/observatory && just test
    @printf "\033[0;32m✓ All tests passed\033[0m\n"
    @echo ""

# Run tests for a specific service
test service:
    @echo ""
    cd services/{{service}} && just test
    @echo ""

# Run CI (all services by default, or one specific service)
ci service="all":
    #!/usr/bin/env bash
    set -e
    printf "\n"
    if [ "{{service}}" = "all" ]; then
        just ci-all
    else
        cd services/{{service}} && just ci
    fi
    printf "\n"

# Run CI checks for all services (verbose)
ci-all:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running CI for All Services ===\033[0m\n"
    printf "\n"

    services=(identity central-bank task-board reputation court observatory)
    for svc in "${services[@]}"; do
        cd "$root/services/$svc" && just ci
    done

    printf "\n"
    printf "\033[0;32m✓ All CI checks passed\033[0m\n"
    printf "\n"

# Run CI checks for all services (quiet mode)
ci-all-quiet:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running CI for All Services (Quiet Mode) ===\033[0m\n"

    services=(identity central-bank task-board reputation court observatory)
    for svc in "${services[@]}"; do
        printf "Checking %s...\n" "$svc"
        cd "$root/services/$svc" && just ci-quiet
    done

    printf "\n"
    printf "\033[0;32m✓ All CI checks passed\033[0m\n"
    printf "\n"

# Run CI checks quietly (alias for pre-commit hook)
ci-quiet:
    @just ci-all-quiet

# Run e2e tests (requires running services)
test-e2e:
    @echo ""
    @printf "\033[0;34m=== Running E2E Tests ===\033[0m\n"
    cd agents && just test-e2e
    @printf "\033[0;32m✓ E2E tests passed\033[0m\n"
    @echo ""

# Show Python lines of code per service
stats:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Python Lines of Code ===\033[0m\n"
    printf "\n"

    total=0

    count_loc() {
        local name=$1
        local dir=$2
        if [ -d "$dir" ]; then
            lines=$(find "$dir" -name '*.py' -type f | xargs cat 2>/dev/null | wc -l | tr -d ' ')
        else
            lines=0
        fi
        total=$((total + lines))
        printf "  \033[0;37m%-20s\033[0m %'6d lines\n" "$name" "$lines"
    }

    for dir in services/*/; do
        name=$(basename "$dir")
        count_loc "$name" "$dir/src"
    done
    count_loc "service-commons" libs/service-commons/src
    count_loc "agents" agents/src

    printf "\n"
    printf "  \033[0;37m%-20s\033[0m \033[1;33m%'6d lines\033[0m\n" "TOTAL" "$total"

    printf "\n"
    printf "\033[0;34m=== Documentation (Markdown) ===\033[0m\n"
    printf "\n"

    if [ -d "docs" ]; then
        doc_lines=$(find docs -name '*.md' -type f | xargs cat 2>/dev/null | wc -l | tr -d ' ')
    else
        doc_lines=0
    fi
    printf "  \033[0;37m%-20s\033[0m %'6d lines\n" "docs/" "$doc_lines"
    printf "\n"

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
