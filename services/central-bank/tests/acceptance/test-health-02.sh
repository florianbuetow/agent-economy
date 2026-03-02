#!/bin/bash
# test-health-02.sh — HEALTH-02: Account count updates after creation
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HEALTH-02" "Account count updates after creation"

step "Register agent on Identity service"
jws_keygen
identity_register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

step "Create account via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$ALICE_ID" 0

step "Assert total_accounts incremented"
http_get "/health"
assert_status "200"
assert_json_eq ".status" "ok"
assert_json_eq ".total_accounts" "1"
assert_json_eq ".total_escrowed" "0"

test_end

