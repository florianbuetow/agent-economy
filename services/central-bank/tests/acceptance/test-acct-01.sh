#!/bin/bash
# test-acct-01.sh — ACCT-01: Create account success
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-01" "Create account success"

step "Register agent on Identity service"
jws_keygen
AGENT_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

step "Create account via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$ALICE_ID" 100

step "Assert account created"
assert_status "201"
assert_json_eq ".account_id" "$ALICE_ID"
assert_json_eq ".balance" "100"
assert_json_exists ".created_at"

test_end

