#!/usr/bin/env bash
set -euo pipefail

BASE_URL="${IDENTITY_BASE_URL:-http://localhost:8001}"
TEST_GLOB="tests/acceptance/test-*.sh"
RUN_LOG="/tmp/identity-acceptance-run.log"
TMP_DIRS=()

wait_for_health() {
    local attempts=40
    local sleep_seconds=0.25
    local i

    for ((i = 1; i <= attempts; i++)); do
        if curl -fsS "$BASE_URL/health" >/dev/null 2>&1; then
            return 0
        fi
        sleep "$sleep_seconds"
    done

    return 1
}

stop_service() {
    just kill >/dev/null 2>&1 || true
}

cleanup() {
    local tmp_dir

    stop_service
    for tmp_dir in "${TMP_DIRS[@]:-}"; do
        rm -rf "$tmp_dir"
    done
    unset CONFIG_PATH || true
}

trap cleanup EXIT

for f in $TEST_GLOB; do
    echo "=== Running $f ==="

    stop_service

    test_tmp_dir="$(mktemp -d)"
    TMP_DIRS+=("$test_tmp_dir")
    test_db_path="$test_tmp_dir/identity.db"
    test_config_path="$test_tmp_dir/config.yaml"

    cat >"$test_config_path" <<EOF
# Identity Service Configuration
# Environment variable overrides use prefix: IDENTITY__

service:
  name: "identity"
  version: "0.1.0"

server:
  host: "0.0.0.0"
  port: 8001
  log_level: "info"

logging:
  level: "INFO"
  format: "json"

database:
  path: "$test_db_path"

crypto:
  algorithm: "ed25519"
  public_key_prefix: "ed25519:"
  public_key_bytes: 32
  signature_bytes: 64

request:
  max_body_size: 1572864
EOF

    export CONFIG_PATH="$test_config_path"
    just run >"$RUN_LOG" 2>&1 &
    run_pid=$!

    if ! wait_for_health; then
        echo "FAILED: service did not become healthy for $f"
        tail -n 60 "$RUN_LOG" || true
        stop_service
        wait "$run_pid" >/dev/null 2>&1 || true
        exit 1
    fi

    if ! bash "$f"; then
        echo "FAILED: $f"
        stop_service
        wait "$run_pid" >/dev/null 2>&1 || true
        exit 1
    fi

    stop_service
    wait "$run_pid" >/dev/null 2>&1 || true
done

echo "ALL TESTS PASSED"
