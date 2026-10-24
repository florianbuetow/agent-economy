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

# --- Service ports (single source; used via {{port_name}} interpolation below) ---
port_identity := "8001"
port_central_bank := "8002"
port_task_board := "8003"
port_reputation := "8004"
port_court := "8005"
port_db_gateway := "8007"
port_ui := "8008"

# Default recipe: show help
_default:
    @just help

# Show available commands
[private]
help:
    @echo ""
    @printf "\033[0;34m=== Agent Task Economy ===\033[0m\n"
    @echo ""
    @printf "\033[1;33mSetup\033[0m\n"
    @printf "  \033[0;37mjust check            \033[0;34m Check if all required tools are installed\033[0m\n"
    @printf "  \033[0;37mjust init-all         \033[0;34m Initialize all project environments\033[0m\n"
    @printf "  \033[0;37mjust destroy-all      \033[0;34m Destroy all virtual environments\033[0m\n"
    @printf "  \033[0;37mjust setup-guard      \033[0;34m Set up guard file integrity collections\033[0m\n"
    @echo ""
    @printf "\033[1;33mLocal Development\033[0m\n"
    @printf "  \033[0;37mjust start-all        \033[0;34m Start all services in background\033[0m\n"
    @printf "  \033[0;37mjust provision-keys   \033[0;34m Explicitly provision configured local agent keys\033[0m\n"
    @printf "  \033[0;37mjust check-keys       \033[0;34m Validate configured local agent keys without changes\033[0m\n"
    @printf "  \033[0;37mjust adopt-key-permissions\033[0;34m Explicitly harden valid legacy private keys to 0600\033[0m\n"
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
    @printf "  \033[0;37mjust start-mathbot [profile]\033[0;34m Start math worker agent from a named profile (default: mathbot; requires services + LM Studio)\033[0m\n"
    @printf "  \033[0;37mjust stop-mathbot [profile]\033[0;34m Stop the exact local math worker profile (default: mathbot)\033[0m\n"
    @printf "  \033[0;37mjust fund-feeder <amount> <operation-id> <reference>\033[0;34m Fund the feeder agent\033[0m\n"
    @printf "  \033[0;37mjust provision        \033[0;34m Provision the treasury (idempotent; run once after first start-all)\033[0m\n"
    @printf "  \033[0;37mjust status           \033[0;34m Check health status of all services\033[0m\n"
    @printf "  \033[0;37mjust logs             \033[0;34m Tail all service logs (color-coded)\033[0m\n"
    @echo ""
    @printf "\033[1;33mTask Generation\033[0m\n"
    @printf "  \033[0;37mjust generate-tasks    \033[0;34m Replace data/math_tasks.jsonl with generated math tasks\033[0m\n"
    @echo ""
    @printf "\033[1;33mDemo\033[0m\n"
    @printf "  \033[0;37mjust demo             \033[0;34m Run a new quick demo (3 agents, ~25s)\033[0m\n"
    @printf "  \033[0;37mjust demo-scale       \033[0;34m Run a new scaled demo (10 agents, ~60s)\033[0m\n"
    @printf "  \033[0;37mjust demo-resume <run-id>\033[0;34m Resume a quick demo\033[0m\n"
    @printf "  \033[0;37mjust demo-scale-resume <run-id>\033[0;34m Resume a scaled demo\033[0m\n"
    @echo ""
    @printf "\033[1;33mCI & Code Quality\033[0m\n"
    @printf "  \033[0;37mjust test-all         \033[0;34m Run tests for all services\033[0m\n"
    @printf "  \033[0;37mjust test-architecture\033[0;34m Run architecture tests for all services\033[0m\n"
    @printf "  \033[0;37mjust test-project-structure\033[0;34m Verify all service justfiles are identical\033[0m\n"
    @printf "  \033[0;37mjust test <service>   \033[0;34m Run tests for a specific service\033[0m\n"
    @printf "  \033[0;37mjust ci               \033[0;34m Run ALL CI checks (services, agents, tools, integration, e2e)\033[0m\n"
    @printf "  \033[0;37mjust ci-quiet         \033[0;34m Run ALL CI checks quietly\033[0m\n"
    @printf "  \033[0;37mjust ci-service <svc> \033[0;34m Run CI checks for a specific service\033[0m\n"
    @printf "  \033[0;37mjust code-semgrep-path-policy\033[0;34m Enforce inherited PATH in first-party code\033[0m\n"
    @printf "  \033[0;37mjust ci-quiet-hook    \033[0;34m CI hook for Claude Code (blocks git commit if CI fails)\033[0m\n"
    @printf "  \033[0;37mjust format-all       \033[0;34m Auto-format all services\033[0m\n"
    @printf "  \033[0;37mjust test-integration \033[0;34m Run cross-service integration tests\033[0m\n"
    @printf "  \033[0;37mjust test-integration-all\033[0;34m Run every service's own integration suite (live stack)\033[0m\n"
    @printf "  \033[0;37mjust test-ui-e2e      \033[0;34m Run UI Playwright e2e suite (self-contained, no live stack)\033[0m\n"
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
    check_tool "curl"    curl    "--version"
    check_tool "jq"      jq      "--version"
    check_tool "lsof"    lsof    "-v"
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

# Initialize all service environments
init-all:
    @echo ""
    @printf "\033[0;34m=== Initializing All Environments ===\033[0m\n"
    cd libs/service-commons && just init
    cd libs/service-clients && just init
    cd libs/service-auth && just init
    cd services/identity && just init
    cd services/central-bank && just init
    cd services/task-board && just init
    cd services/reputation && just init
    cd services/court && just init
    cd services/db-gateway && just init
    cd services/ui && just init
    cd agents && just init
    cd tools && just init
    @printf "\033[0;32m✓ All environments initialized\033[0m\n"
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

# Internal background launches used by start-all. These intentionally omit
# hot reload while preserving Court's explicit local environment loading.
_launch-db-gateway:
    cd services/db-gateway && uv run uvicorn db_gateway_service.app:create_app --factory --host 127.0.0.1 --port {{port_db_gateway}}

_launch-identity:
    cd services/identity && uv run uvicorn identity_service.app:create_app --factory --host 127.0.0.1 --port {{port_identity}}

_launch-reputation:
    cd services/reputation && uv run uvicorn reputation_service.app:create_app --factory --host 127.0.0.1 --port {{port_reputation}}

_launch-bank:
    cd services/central-bank && uv run uvicorn central_bank_service.app:create_app --factory --host 127.0.0.1 --port {{port_central_bank}}

_launch-taskboard:
    cd services/task-board && uv run uvicorn task_board_service.app:create_app --factory --host 127.0.0.1 --port {{port_task_board}}

_launch-court:
    #!/usr/bin/env bash
    cd services/court
    if [ -f .env ]; then
        set -a
        . .env
        set +a
    fi
    uv run uvicorn court_service.app:create_app --factory --host 127.0.0.1 --port {{port_court}}

_launch-ui:
    cd services/ui && uv run uvicorn ui_service.app:create_app --factory --host 127.0.0.1 --port {{port_ui}}

# Start all services in background (respects dependency order)
start-all:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Starting All Services (Background) ===\033[0m\n"
    printf "\n"

    if ! just check-keys; then
        printf "\033[0;31m✗ Key preflight failed before service startup.\033[0m\n"
        printf "Run 'just provision-keys' for an empty key store.\n"
        printf "For valid legacy 0644 keys, run 'just adopt-key-permissions' explicitly.\n"
        exit 1
    fi

    wait_for_health() {
        local name=$1
        local port=$2
        local max_attempts=60
        local attempt=0
        while [ $attempt -lt $max_attempts ]; do
            if curl --fail --silent --max-time 2 --connect-timeout 1 \
                "http://localhost:${port}/health" 2>/dev/null \
                | jq -e '.status == "ok"' >/dev/null 2>&1; then
                printf "\033[0;32m✓ %s ready (port %s)\033[0m\n" "$name" "$port"
                return 0
            fi
            attempt=$((attempt + 1))
            sleep 0.3
        done
        printf "\033[0;31m✗ %s failed to start on port %s\033[0m\n" "$name" "$port"
        return 1
    }

    is_healthy() {
        local port=$1
        curl --fail --silent --max-time 2 --connect-timeout 1 \
            "http://localhost:${port}/health" 2>/dev/null \
            | jq -e '.status == "ok"' >/dev/null 2>&1
    }

    start_unless_healthy() {
        local name=$1
        local port=$2
        local recipe=$3
        if is_healthy "$port"; then
            printf "\033[0;32m✓ %s already healthy (port %s)\033[0m\n" "$name" "$port"
            return 0
        fi
        just "$recipe" &
    }

    # Tier 1: DB Gateway first (creates economy.db needed by UI)
    printf "Starting tier 1 (DB Gateway)...\n"
    start_unless_healthy "DB Gateway" {{port_db_gateway}} _launch-db-gateway
    if ! wait_for_health "DB Gateway" {{port_db_gateway}}; then
        printf "\033[0;31m✗ DB Gateway did not become healthy; aborting startup\033[0m\n"
        exit 1
    fi

    # Tier 2: Identity first. It is the leaf service that central-bank,
    # task-board, reputation, and court each register their platform agent
    # against during startup. Launching it concurrently with those dependents
    # caused a race: a dependent could POST /agents/register before Identity
    # had bound its socket, raising httpx.ConnectError and aborting that
    # service's lifespan ("Application startup failed. Exiting.").
    printf "Starting tier 2 (Identity)...\n"
    start_unless_healthy "Identity" {{port_identity}} _launch-identity
    if ! wait_for_health "Identity" {{port_identity}}; then
        printf "\033[0;31m✗ Identity did not become healthy; aborting startup\033[0m\n"
        exit 1
    fi
    # Fully-online gate: confirm the registry actually serves reads, not just
    # that the port is bound. Dependents will POST /agents/register, so assert
    # GET /agents answers before launching them.
    if ! curl --fail --silent --max-time 2 --connect-timeout 1 \
        "http://localhost:{{port_identity}}/agents" 2>/dev/null \
        | jq -e '.agents | type == "array"' >/dev/null 2>&1; then
        printf "\033[0;31m✗ Identity health OK but /agents not serving; aborting startup\033[0m\n"
        exit 1
    fi
    printf "\033[0;32m✓ Identity registry fully online (port {{port_identity}})\033[0m\n"

    # Tier 3: economy services in parallel. Identity is healthy, so
    # platform-agent registration can no longer race.
    printf "Starting tier 3 (economy services)...\n"
    start_unless_healthy "Reputation" {{port_reputation}} _launch-reputation
    start_unless_healthy "Central Bank" {{port_central_bank}} _launch-bank
    start_unless_healthy "Task Board" {{port_task_board}} _launch-taskboard
    start_unless_healthy "Court" {{port_court}} _launch-court

    # Wait for the rest in dependency order
    if ! wait_for_health "Central Bank" {{port_central_bank}}; then
        printf "\033[0;31m✗ Central Bank did not become healthy; aborting startup\033[0m\n"
        exit 1
    fi
    if ! wait_for_health "Task Board" {{port_task_board}}; then
        printf "\033[0;31m✗ Task Board did not become healthy; aborting startup\033[0m\n"
        exit 1
    fi
    if ! wait_for_health "Reputation" {{port_reputation}}; then
        printf "\033[0;31m✗ Reputation did not become healthy; aborting startup\033[0m\n"
        exit 1
    fi
    if ! wait_for_health "Court" {{port_court}}; then
        printf "\033[0;31m✗ Court did not become healthy; aborting startup\033[0m\n"
        exit 1
    fi

    # Tier 4: UI last, after every service it calls is healthy.
    printf "Starting tier 4 (UI)...\n"
    start_unless_healthy "UI" {{port_ui}} _launch-ui
    if ! wait_for_health "UI" {{port_ui}}; then
        printf "\033[0;31m✗ UI did not become healthy; aborting startup\033[0m\n"
        exit 1
    fi

    printf "\n"
    printf "\033[0;32m✓ All services started\033[0m\n"
    printf "\033[0;32m  UI Service: http://localhost:{{port_ui}}\033[0m\n"
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
        if ! (cd "services/$svc" && just kill); then
            failed=$((failed + 1))
        fi
    done

    if [ "$failed" -gt 0 ]; then
        printf "\033[0;31m✗ %d service(s) could not be stopped\033[0m\n" "$failed"
        printf "\n"
        exit 1
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
    repo_root=$PWD
    agent_config=${AGENT_CONFIG_PATH:-"$repo_root/agents/config.yaml"}
    if ! uv run --project agents scripts/stop_agent.py \
        --repo-root "$repo_root" \
        --config "$agent_config" \
        --module task_feeder; then
        printf "\033[0;31m✗ Task feeder stop refused: ownership could not be proven\033[0m\n"
        exit 1
    fi
    printf "\033[0;32m✓ Task feeder termination requested safely\033[0m\n"
    printf "\n"

# Start math worker agent from a named profile (requires running services + LM Studio)
start-mathbot profile="mathbot":
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Starting Math Worker Agent (profile: %s) ===\033[0m\n" {{quote(profile)}}
    printf "\n"
    cd agents && uv run python -m math_worker {{quote(profile)}} &
    printf "Math worker agent starting in background (PID: $!)\n"
    printf "\n"

# Stop one exact math worker profile
stop-mathbot profile="mathbot":
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Stopping Math Worker Agent (profile: %s) ===\033[0m\n" {{quote(profile)}}
    repo_root=$PWD
    agent_config=${AGENT_CONFIG_PATH:-"$repo_root/agents/config.yaml"}
    if ! uv run --project agents scripts/stop_agent.py \
        --repo-root "$repo_root" \
        --config "$agent_config" \
        --module math_worker \
        --profile {{quote(profile)}}; then
        printf "\033[0;31m✗ Math worker stop refused: ownership could not be proven\033[0m\n"
        exit 1
    fi
    printf "\033[0;32m✓ Math worker termination requested safely\033[0m\n"
    printf "\n"

# Fund the feeder agent with initial coins
fund-feeder amount operation_id reference:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Funding Feeder Agent ===\033[0m\n"
    printf "\n"
    cd agents && uv run python -m fund_feeder_cli {{quote(amount)}} --operation-id {{quote(operation_id)}} --reference {{quote(reference)}}
    exit_code=$?
    printf "\n"
    if [ $exit_code -eq 0 ]; then
        printf "\033[0;32m✓ Feeder agent funded successfully\033[0m\n"
    else
        printf "\033[0;31m✗ Failed to fund feeder agent\033[0m\n"
        exit 1
    fi
    printf "\n"

# Provision the treasury (idempotent genesis for the UI operator account).
# Q-9 decision: `just start-all` does NOT auto-mint the treasury any more —
# run this once after the first `just start-all` (and again any time you
# need to confirm/re-assert genesis; re-running is a safe no-op).
provision:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Provisioning Treasury ===\033[0m\n"
    printf "\n"
    cd agents && uv run python -m treasury_provision_cli
    exit_code=$?
    printf "\n"
    if [ $exit_code -eq 0 ]; then
        printf "\033[0;32m✓ Treasury provisioned successfully\033[0m\n"
    else
        printf "\033[0;31m✗ Failed to provision treasury\033[0m\n"
        exit 1
    fi
    printf "\n"

# Explicitly provision every handle in the configured local roster.
provision-keys:
    #!/usr/bin/env bash
    set -euo pipefail
    cd agents
    uv run python -m key_provision_cli provision \
        --gateway-config ../services/db-gateway/config.yaml

# Validate every configured local keypair without changing key material.
check-keys:
    #!/usr/bin/env bash
    set -euo pipefail
    cd agents
    uv run python -m key_provision_cli check \
        --gateway-config ../services/db-gateway/config.yaml

# Explicit one-time adoption for valid legacy private keys with permissive modes.
adopt-key-permissions:
    #!/usr/bin/env bash
    set -euo pipefail
    cd agents
    uv run python -m key_provision_cli adopt-permissions \
        --gateway-config ../services/db-gateway/config.yaml

# Check health status of all services
status:
    #!/usr/bin/env bash
    printf "\n"
    printf "\033[0;34m=== Service Status ===\033[0m\n"
    printf "\n"
    failed=0

    check_service() {
        local name=$1
        local port=$2
        local health_response

        local status
        local uptime
        local agents

        if health_response=$(curl --fail --silent --max-time 3 --connect-timeout 2 \
            "http://localhost:${port}/health" 2>/dev/null); then
            if ! status=$(printf "%s" "$health_response" | jq -er '.status' 2>/dev/null); then
                printf "\033[0;31m✗ %s\033[0m (port %s) - malformed health response\n" "$name" "$port"
                failed=$((failed + 1))
                return
            fi
            if [ "$status" = "ok" ]; then
                uptime=$(printf "%s" "$health_response" | jq -r '.uptime_seconds // empty')
                agents=$(printf "%s" "$health_response" | jq -r '.registered_agents // empty')
                printf "\033[0;32m✓ %s\033[0m (port %s) - ok" "$name" "$port"
                [ -n "$uptime" ] && printf " (uptime: %ss)" "$uptime"
                [ -n "$agents" ] && printf " (agents: %s)" "$agents"
                printf "\n"
            else
                printf "\033[0;31m✗ %s\033[0m (port %s) - %s\n" "$name" "$port" "$status"
                failed=$((failed + 1))
            fi
        else
            printf "\033[0;31m✗ %s\033[0m (port %s) - not responding\n" "$name" "$port"
            failed=$((failed + 1))
        fi
    }

    check_service "Identity"     {{port_identity}}
    check_service "Central Bank" {{port_central_bank}}
    check_service "Task Board"   {{port_task_board}}
    check_service "Reputation"   {{port_reputation}}
    check_service "Court"        {{port_court}}
    check_service "DB Gateway"   {{port_db_gateway}}
    check_service "UI"           {{port_ui}}

    printf "\n"
    if [ "$failed" -gt 0 ]; then
        printf "\033[0;31m✗ %d service(s) unhealthy\033[0m\n" "$failed"
        exit 1
    fi
    printf "\033[0;32m✓ All services healthy\033[0m\n"
    printf "\n"

# Tail all service logs (color-coded, today by default)
logs date="":
    #!/usr/bin/env bash
    printf "\n"
    if [ -n {{quote(date)}} ]; then
        uv run --project services/db-gateway python scripts/tail_logs.py --date {{quote(date)}}
    else
        uv run --project services/db-gateway python scripts/tail_logs.py
    fi

# Destroy all virtual environments
destroy-all:
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
    cd libs/service-auth && just destroy
    cd libs/service-clients && just destroy
    cd libs/service-commons && just destroy
    @printf "\033[0;32m✓ All virtual environments removed\033[0m\n"
    @echo ""

# --- Task Generation ---

# Generate a replacement set of 10,000 tasks in data/math_tasks.jsonl
generate-tasks:
    @echo ""
    cd tools && uv run python -m math_task_factory --total 10000
    @printf "\033[0;32m✓ Replaced data/math_tasks.jsonl with generated tasks\033[0m\n"
    @echo ""

# --- Demo ---

# Run a new quick scenario against the existing stack
demo:
    uv run --project services/db-gateway python scripts/open_ui.py --url http://localhost:{{port_ui}}
    cd tools && uv run python -m demo_replay scenarios/quick.yaml

# Run a new scale scenario against the existing stack
demo-scale:
    uv run --project services/db-gateway python scripts/open_ui.py --url http://localhost:{{port_ui}}
    cd tools && uv run python -m demo_replay scenarios/scale.yaml

# Resume a quick scenario against the existing stack
demo-resume run_id:
    uv run --project services/db-gateway python scripts/open_ui.py --url http://localhost:{{port_ui}}
    cd tools && uv run python -m demo_replay scenarios/quick.yaml --run-id {{quote(run_id)}}

# Resume a scale scenario against the existing stack
demo-scale-resume run_id:
    uv run --project services/db-gateway python scripts/open_ui.py --url http://localhost:{{port_ui}}
    cd tools && uv run python -m demo_replay scenarios/scale.yaml --run-id {{quote(run_id)}}

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
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running All Tests ===\033[0m\n"
    printf "\n"
    uv run --directory "$root/services/db-gateway" \
        "$root/scripts/isolated_test_runtime.py" -- \
        just _test-all-suites

[private]
_test-all-suites:
    #!/usr/bin/env bash
    set -uo pipefail
    root="$(pwd)"
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

# Run tests for a specific service
test service:
    #!/usr/bin/env bash
    set -euo pipefail
    service={{quote(service)}}
    case "$service" in
        identity|central-bank|task-board|reputation|court|db-gateway|ui) ;;
        *) printf "\033[0;31m✗ Unknown service: %s\033[0m\n" "$service" >&2; exit 1 ;;
    esac
    printf "\n"
    cd "services/$service"
    just test
    printf "\n"

# Run CI checks for a specific service
ci-service service:
    #!/usr/bin/env bash
    set -euo pipefail
    service={{quote(service)}}
    case "$service" in
        identity|central-bank|task-board|reputation|court|db-gateway|ui) ;;
        *) printf "\033[0;31m✗ Unknown service: %s\033[0m\n" "$service" >&2; exit 1 ;;
    esac
    printf "\n"
    cd "services/$service"
    just ci
    printf "\n"

# Test and enforce the repository-wide inherited-PATH policy
code-semgrep-path-policy:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    verification_root="${TMPDIR:?TMPDIR must name writable verification storage}/agent-economy-semgrep"
    mkdir -p "$verification_root/config" "$verification_root/cache" "$verification_root/uv-cache"
    if [ -r /etc/ssl/cert.pem ]; then
        ca_bundle=/etc/ssl/cert.pem
    elif [ -r /etc/ssl/certs/ca-certificates.crt ]; then
        ca_bundle=/etc/ssl/certs/ca-certificates.crt
    else
        printf 'Semgrep requires a readable system CA bundle.\n' >&2
        exit 1
    fi
    semgrep=(
        uv run --project "$root/services/db-gateway" --all-extras --frozen
        semgrep --metrics off
    )
    semgrep_environment=(
        "TMPDIR=$TMPDIR"
        "UV_CACHE_DIR=$verification_root/uv-cache"
        "XDG_CONFIG_HOME=$verification_root/config"
        "XDG_CACHE_HOME=$verification_root/cache"
        "SEMGREP_SETTINGS_FILE=$verification_root/settings.yml"
        "SEMGREP_LOG_FILE=$verification_root/semgrep.log"
        "SEMGREP_SEND_METRICS=off"
        "SEMGREP_ENABLE_VERSION_CHECK=0"
        "SSL_CERT_FILE=$ca_bundle"
    )

    for fixture in \
        "$root/config/semgrep/tests/no-path-overrides/python.py" \
        "$root/config/semgrep/tests/no-path-overrides/shell.sh"; do
        env "${semgrep_environment[@]}" "${semgrep[@]}" --test \
            --config "$root/config/semgrep/no-path-overrides.yml" "$fixture"
    done

    targets=(
        "$root/agents"
        "$root/libs"
        "$root/services"
        "$root/tools"
        "$root/tests"
        "$root/scripts"
        "$root/.githooks"
    )
    env "${semgrep_environment[@]}" "${semgrep[@]}" \
        --config "$root/config/semgrep/no-path-overrides.yml" --error --no-git-ignore \
        --exclude '**/.venv/**' --exclude '**/node_modules/**' \
        --exclude '**/build/**' --exclude '**/dist/**' \
        --exclude '**/__pycache__/**' "${targets[@]}"
    printf '\033[0;32m✓ Repository PATH policy passed\033[0m\n'

# Run ALL CI checks: services, agents, tools, cross-service integration tests, e2e tests
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

    printf "\033[0;34m--- Phase 0b: Repository PATH policy ---\033[0m\n"
    cd "$root" && just code-semgrep-path-policy

    # Phase 1: Libs CI (format, lint, types, security, spell, unit tests)
    printf "\033[0;34m--- Phase 1: Libs CI ---\033[0m\n"
    libs=(service-commons service-clients service-auth)
    for lib in "${libs[@]}"; do
        cd "$root/libs/$lib" && just ci
    done

    # Phase 2: Per-service CI (format, lint, types, security, deps, spell, semgrep, audit, tests, pyright)
    printf "\033[0;34m--- Phase 2: Service CI ---\033[0m\n"
    services=(identity central-bank task-board reputation court db-gateway ui)
    for svc in "${services[@]}"; do
        cd "$root/services/$svc" && just ci
    done

    # Phase 3: Per-service integration tests (T-071/GAP-F1; live stack, stack-up
    # pattern from commit 2a907b1)
    printf "\033[0;34m--- Phase 3: Per-service integration tests ---\033[0m\n"
    cd "$root" && just test-integration-all

    # Phase 4: Agents CI (format, lint, types, security, spell, unit tests)
    printf "\033[0;34m--- Phase 4: Agents CI ---\033[0m\n"
    cd "$root/agents" && just ci

    # Phase 5: Tools CI (format, lint, types, security, spell, unit tests)
    printf "\033[0;34m--- Phase 5: Tools CI ---\033[0m\n"
    cd "$root/tools" && just ci

    # Phase 6: Cross-service and isolated-runtime acceptance tests
    printf "\033[0;34m--- Phase 6: Cross-service and runtime integration tests ---\033[0m\n"
    cd "$root"
    PYTHONPATH="$root:$root/tests" uv run --directory "$root/services/db-gateway" \
        pytest "$root/tests/integration/" "$root/tests/runtime/" -v --tb=short

    # Phase 7: UI Playwright e2e (self-contained; no live stack)
    printf "\033[0;34m--- Phase 7: UI Playwright e2e ---\033[0m\n"
    cd "$root"
    just test-ui-e2e

    # Phase 8: E2E tests (restarts services, runs full lifecycle tests)
    printf "\033[0;34m--- Phase 8: E2E tests ---\033[0m\n"
    cd "$root"
    just test-e2e

    printf "\n"
    printf "\033[0;32m✓ Full CI passed (libs + services + agents + tools + integration + ui-e2e + e2e)\033[0m\n"
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

    printf "\033[0;34m--- Phase 0b: Repository PATH policy ---\033[0m\n"
    cd "$root" && just code-semgrep-path-policy

    # Phase 1: Libs CI
    printf "\033[0;34m--- Phase 1: Libs CI ---\033[0m\n"
    libs=(service-commons service-clients service-auth)
    for lib in "${libs[@]}"; do
        printf "Checking %s...\n" "$lib"
        cd "$root/libs/$lib" && just ci-quiet
    done

    # Phase 2: Per-service CI
    printf "\033[0;34m--- Phase 2: Service CI ---\033[0m\n"
    services=(identity central-bank task-board reputation court db-gateway ui)
    for svc in "${services[@]}"; do
        printf "Checking %s...\n" "$svc"
        cd "$root/services/$svc" && just ci-quiet
    done

    # Phase 3: Per-service integration tests (T-071/GAP-F1; live stack, stack-up
    # pattern from commit 2a907b1)
    printf "\033[0;34m--- Phase 3: Per-service integration tests ---\033[0m\n"
    cd "$root" && just test-integration-all

    # Phase 4: Agents CI
    printf "\033[0;34m--- Phase 4: Agents CI ---\033[0m\n"
    cd "$root/agents" && just ci-quiet

    # Phase 5: Tools CI
    printf "\033[0;34m--- Phase 5: Tools CI ---\033[0m\n"
    cd "$root/tools" && just ci-quiet

    # Phase 6: Cross-service and isolated-runtime acceptance tests
    printf "\033[0;34m--- Phase 6: Cross-service and runtime integration tests ---\033[0m\n"
    cd "$root"
    PYTHONPATH="$root:$root/tests" uv run --directory "$root/services/db-gateway" \
        pytest "$root/tests/integration/" "$root/tests/runtime/" -v --tb=short

    # Phase 7: UI Playwright e2e (self-contained; no live stack)
    printf "\033[0;34m--- Phase 7: UI Playwright e2e ---\033[0m\n"
    cd "$root"
    just test-ui-e2e

    # Phase 8: E2E tests
    printf "\033[0;34m--- Phase 8: E2E tests ---\033[0m\n"
    cd "$root"
    just test-e2e

    printf "\n"
    printf "\033[0;32m✓ Full CI passed (libs + services + agents + tools + integration + ui-e2e + e2e)\033[0m\n"
    printf "\n"

# CI hook for Claude Code — blocks git commit if CI fails
ci-quiet-hook:
    @bash scripts/ci-quiet-hook.sh

# Run cross-service integration and isolated-runtime acceptance tests
test-integration:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running Cross-Service Integration Tests ===\033[0m\n"
    printf "\n"
    PYTHONPATH="$root:$root/tests" uv run --directory "$root/services/db-gateway" \
        pytest "$root/tests/integration/" "$root/tests/runtime/" -v --tb=short
    printf "\n"
    printf "\033[0;32m✓ Cross-service integration tests passed\033[0m\n"
    printf "\n"

# Run every service's tests/integration/ suite against a disposable live stack.
# Most suites are self-contained; DB Gateway's suite consumes the generated
# DB_GATEWAY_URL from this run.
test-integration-all:
    #!/usr/bin/env bash
    set -euo pipefail
    root="$(pwd)"
    printf "\n"
    printf "\033[0;34m=== Running Per-Service Integration Tests ===\033[0m\n"
    printf "\n"
    uv run --directory "$root/services/db-gateway" \
        "$root/scripts/isolated_test_runtime.py" -- \
        just _test-integration-suites

[private]
_test-integration-suites:
    #!/usr/bin/env bash
    set -uo pipefail
    root="$(pwd)"
    fail=0
    services=(identity central-bank task-board reputation court db-gateway ui)
    for svc in "${services[@]}"; do
        printf "\033[0;34m--- %s ---\033[0m\n" "$svc"
        cd "$root/services/$svc" && just test-integration || fail=1
        cd "$root"
    done

    printf "\n"
    if [ "$fail" -ne 0 ]; then
        printf "\033[0;31m✗ Some per-service integration suites failed\033[0m\n"
        exit 1
    fi
    printf "\033[0;32m✓ Per-service integration tests passed\033[0m\n"
    printf "\n"

# Run the UI service's Playwright E2E suite (browser-based; fully self-contained
# — its own conftest spins up a temp DB + a UI server subprocess on a random
# port, so it needs no live stack and does not call start-all/stop-all)
test-ui-e2e:
    #!/usr/bin/env bash
    set -euo pipefail
    printf "\n"
    printf "\033[0;34m=== Running UI Playwright E2E Tests ===\033[0m\n"
    printf "\n"

    root="$(pwd)"
    cd "$root/services/ui"

    just test-prerequisites

    uv run pytest tests/e2e -v --tb=short

    printf "\n"
    printf "\033[0;32m✓ UI Playwright E2E tests passed\033[0m\n"
    printf "\n"

# Run e2e tests against a disposable isolated stack, then tear down only that stack.
test-e2e:
    #!/usr/bin/env bash
    set -euo pipefail
    printf "\n"
    printf "\033[0;34m=== Running E2E Tests ===\033[0m\n"
    printf "\n"

    root="$(pwd)"
    uv run --directory "$root/services/db-gateway" \
        "$root/scripts/isolated_test_runtime.py" --provision -- \
        just --justfile "$root/agents/justfile" --working-directory "$root/agents" test-e2e
    printf "\n"
    printf "\033[0;32m✓ E2E tests passed\033[0m\n"
    printf "\n"

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
