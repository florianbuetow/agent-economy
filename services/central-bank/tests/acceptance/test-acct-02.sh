#!/bin/bash
# test-acct-02.sh — ACCT-02: Create account with zero balance
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-02" "Create account with zero balance"

step "Register agent on Identity service"
jws_keygen
identity_register_agent "ZeroCase" "$PUBLIC_KEY"
AGENT_ID_ZERO="$AGENT_ID"

step "Create account via platform token with initial_balance=0"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$AGENT_ID_ZERO" 0

step "Assert account created with zero balance"
assert_status "201"
assert_json_eq ".account_id" "$AGENT_ID_ZERO"
assert_json_eq ".balance" "0"
assert_json_exists ".created_at"

test_end

