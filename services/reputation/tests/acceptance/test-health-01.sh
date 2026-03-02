#!/bin/bash
# test-health-01.sh — HEALTH-01: Health schema is correct
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HEALTH-01" "Health schema is correct"

step "GET /health"
http_get "/health"

step "Assert 200 with correct schema"
assert_status "200"
assert_json_exists ".status"
assert_json_exists ".uptime_seconds"
assert_json_exists ".started_at"
assert_json_exists ".total_feedback"
assert_json_eq ".status" "ok"

test_end
