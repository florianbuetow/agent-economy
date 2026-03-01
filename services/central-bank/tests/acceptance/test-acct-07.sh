#!/bin/bash
# test-acct-07.sh — ACCT-07: Get balance forbidden for other agent's account
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-07" "Get balance forbidden for other agent's account"

step "Register agents on Identity service"
jws_keygen
ALICE_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

jws_keygen
BOB_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Bob" "$PUBLIC_KEY"
BOB_ID="$AGENT_ID"

step "Create accounts via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$ALICE_ID" 10
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$BOB_ID" 0

step "Bob attempts to read Alice's account"
PAYLOAD=$(jq -nc '{action:"get_balance"}')
jws_sign "$BOB_PRIV" "$BOB_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$ALICE_ID" "$JWS_TOKEN"

step "Assert forbidden"
assert_status "403"
assert_json_eq ".error" "FORBIDDEN"
assert_error_envelope

test_end

