#!/bin/bash
# test-health-01.sh — HEALTH-01: Health schema is correct
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HEALTH-01" "Health schema is correct"

step "Call health endpoint"
http_get "/health"

step "Assert health response schema"
assert_status "200"
assert_json_eq ".status" "ok"
assert_json_exists ".uptime_seconds"
assert_json_exists ".started_at"
assert_json_exists ".total_accounts"
assert_json_exists ".total_escrowed"
assert_json_eq ".total_accounts" "0"
assert_json_eq ".total_escrowed" "0"

test_end

