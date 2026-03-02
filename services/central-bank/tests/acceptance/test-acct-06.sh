#!/bin/bash
# test-acct-06.sh — ACCT-06: Get balance (agent reads own account)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-06" "Get balance (agent reads own account)"

step "Register agent on Identity service"
jws_keygen
ALICE_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

step "Create account via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$ALICE_ID" 123

step "Get balance using Bearer token"
PAYLOAD=$(jq -nc '{action:"get_balance"}')
jws_sign "$ALICE_PRIV" "$ALICE_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$ALICE_ID" "$JWS_TOKEN"

step "Assert balance response"
assert_status "200"
assert_json_eq ".account_id" "$ALICE_ID"
assert_json_eq ".balance" "123"
assert_json_exists ".created_at"

test_end

