#!/bin/bash
# test-acct-05.sh — ACCT-05: Credit account success (balance increases)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-05" "Credit account success (balance increases)"

step "Register agent on Identity service"
jws_keygen
ALICE_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

step "Create account via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$ALICE_ID" 0

step "Credit account via platform token"
bank_credit_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$ALICE_ID" 25 "bonus"
assert_status "200"
assert_json_matches ".tx_id" "$TX_ID_PATTERN"
assert_json_eq ".balance_after" "25"

step "Assert balance increased"
PAYLOAD=$(jq -nc '{action:"get_balance"}')
jws_sign "$ALICE_PRIV" "$ALICE_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$ALICE_ID" "$JWS_TOKEN"
assert_status "200"
assert_json_eq ".account_id" "$ALICE_ID"
assert_json_eq ".balance" "25"

test_end

