#!/bin/bash
# test-health-03.sh — HEALTH-03: POST /health returns 405
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HEALTH-03" "POST /health returns 405"

step "POST health endpoint"
http_method "POST" "/health"

step "Assert method not allowed"
assert_status "405"
assert_json_eq ".error" "METHOD_NOT_ALLOWED"
assert_error_envelope

test_end

