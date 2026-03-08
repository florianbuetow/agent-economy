#!/usr/bin/env bash
# Helper functions for service management in justfile recipes.
# Source this file: source ../../scripts/service-helpers.sh

# Ensure the service is running. Starts it if not already up.
# Sets STARTED_BY_US=true and SVC_PID if we started it.
# Usage: ensure_service_running <service_module> <port>
ensure_service_running() {
    local service_module="$1"
    local port="$2"
    STARTED_BY_US=false
    SVC_PID=""

    if curl -s --connect-timeout 2 "http://localhost:${port}/health" >/dev/null 2>&1; then
        printf "\033[0;33m→ Service already running on port %s\033[0m\n" "$port"
        return 0
    fi

    printf "\033[0;33m→ Service not running, starting it...\033[0m\n"
    uv run uvicorn "${service_module}.app:create_app" --factory --host 127.0.0.1 --port "$port" &
    SVC_PID=$!
    STARTED_BY_US=true

    for i in $(seq 1 30); do
        if curl -s --connect-timeout 1 "http://localhost:${port}/health" >/dev/null 2>&1; then
            printf "\033[0;32m✓ Service started (PID %s)\033[0m\n" "$SVC_PID"
            return 0
        fi
        sleep 1
    done

    printf "\033[0;31m✗ Service failed to start within 30 seconds\033[0m\n"
    kill "$SVC_PID" 2>/dev/null
    return 1
}

# Stop the service if we started it.
# Usage: stop_service_if_started
stop_service_if_started() {
    if [ "${STARTED_BY_US:-false}" = true ] && [ -n "${SVC_PID:-}" ]; then
        printf "\033[0;33m→ Stopping service (PID %s)...\033[0m\n" "$SVC_PID"
        kill "$SVC_PID" 2>/dev/null
        wait "$SVC_PID" 2>/dev/null || true
    fi
}
