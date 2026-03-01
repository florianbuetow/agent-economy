#!/usr/bin/env bash
set -euo pipefail

IDENTITY_BASE_URL="${IDENTITY_BASE_URL:-http://localhost:8001}"
BANK_BASE_URL="${BANK_BASE_URL:-http://localhost:8002}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SERVICE_DIR="$(cd "$SCRIPT_DIR/../.." && pwd)"
IDENTITY_DIR="$(cd "$SERVICE_DIR/../identity" && pwd)"
JWS_HELPER="$SCRIPT_DIR/jws_helper.py"
TEST_GLOB="tests/acceptance/test-*.sh"
RUN_LOG_IDENTITY="/tmp/central-bank-acceptance-identity.log"
RUN_LOG_BANK="/tmp/central-bank-acceptance-bank.log"
TMP_DIRS=()

wait_for_health() {
    local url="$1"
    local attempts=40
    local sleep_seconds=0.25
    for ((i = 1; i <= attempts; i++)); do
        if curl -fsS "$url/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$sleep_seconds"
    done
    return 1
}

stop_services() {
    cd "$SERVICE_DIR" && just kill >/dev/null 2>&1 || true
    cd "$IDENTITY_DIR" && just kill >/dev/null 2>&1 || true
}

cleanup() {
    local tmp_dir
    stop_services
    for tmp_dir in "${TMP_DIRS[@]:-}"; do
        rm -rf "$tmp_dir"
    done
    unset CONFIG_PATH PLATFORM_AGENT_ID PLATFORM_PRIVATE_KEY_HEX || true
}

trap cleanup EXIT

for f in $TEST_GLOB; do
    echo "=== Running $f ==="

    stop_services

    test_tmp_dir="$(mktemp -d)"
    TMP_DIRS+=("$test_tmp_dir")

    # --- Start Identity service ---
    identity_db_path="$test_tmp_dir/identity.db"
    identity_config_path="$test_tmp_dir/identity-config.yaml"
    cat >"$identity_config_path" <<IDEOF
service:
  name: "identity"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8001
  log_level: "warning"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "$identity_db_path"
crypto:
  algorithm: "ed25519"
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64
request:
  max_body_size: 1572864
IDEOF

    cd "$IDENTITY_DIR"
    CONFIG_PATH="$identity_config_path" just run >"$RUN_LOG_IDENTITY" 2>&1 &
    identity_pid=$!

    if ! wait_for_health "$IDENTITY_BASE_URL"; then
        echo "FAILED: identity service did not become healthy for $f"
        tail -n 60 "$RUN_LOG_IDENTITY" || true
        exit 1
    fi

    # --- Register platform agent on Identity ---
    keygen_output=$(cd "$SERVICE_DIR" && uv run python "$JWS_HELPER" keygen)
    PLATFORM_PRIVATE_KEY_HEX=$(echo "$keygen_output" | sed -n '1p')
    PLATFORM_PUBLIC_KEY=$(echo "$keygen_output" | sed -n '2p')
    export PLATFORM_PRIVATE_KEY_HEX

    register_body=$(jq -nc --arg name "Platform" --arg public_key "$PLATFORM_PUBLIC_KEY" '{name:$name, public_key:$public_key}')
    register_response=$(curl -s -X POST -H "Content-Type: application/json" "${IDENTITY_BASE_URL}/agents/register" -d "$register_body")
    PLATFORM_AGENT_ID=$(echo "$register_response" | jq -r '.agent_id')
    export PLATFORM_AGENT_ID

    if [ -z "$PLATFORM_AGENT_ID" ] || [ "$PLATFORM_AGENT_ID" = "null" ]; then
        echo "FAILED: could not register platform agent for $f"
        echo "  Response: $register_response"
        exit 1
    fi

    # --- Start Central Bank service ---
    bank_db_path="$test_tmp_dir/central-bank.db"
    bank_config_path="$test_tmp_dir/bank-config.yaml"
    cat >"$bank_config_path" <<BANKEOF
service:
  name: "central-bank"
  version: "0.1.0"
server:
  host: "0.0.0.0"
  port: 8002
  log_level: "warning"
logging:
  level: "WARNING"
  format: "json"
database:
  path: "$bank_db_path"
identity:
  base_url: "http://localhost:8001"
  verify_jws_path: "/agents/verify-jws"
  get_agent_path: "/agents"
platform:
  agent_id: "$PLATFORM_AGENT_ID"
request:
  max_body_size: 1048576
BANKEOF

    cd "$SERVICE_DIR"
    CONFIG_PATH="$bank_config_path" just run >"$RUN_LOG_BANK" 2>&1 &
    bank_pid=$!

    if ! wait_for_health "$BANK_BASE_URL"; then
        echo "FAILED: central-bank service did not become healthy for $f"
        tail -n 60 "$RUN_LOG_BANK" || true
        exit 1
    fi

    # --- Run test ---
    cd "$SERVICE_DIR"
    if ! bash "$f"; then
        echo "FAILED: $f"
        stop_services
        exit 1
    fi

    stop_services
    wait "$bank_pid" >/dev/null 2>&1 || true
    wait "$identity_pid" >/dev/null 2>&1 || true
done

echo "ALL TESTS PASSED"
