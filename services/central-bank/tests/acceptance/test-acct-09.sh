#!/bin/bash
# test-acct-09.sh — ACCT-09: Missing token returns 400 INVALID_JWS
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-09" "Missing token returns 400 INVALID_JWS"

step "POST /accounts without token"
http_post "/accounts" '{}'

step "Assert invalid JWS error"
assert_status "400"
assert_json_eq ".error" "INVALID_JWS"
assert_error_envelope

test_end

