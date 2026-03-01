#!/bin/bash
# test-acct-08.sh — ACCT-08: Get transactions returns history
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-08" "Get transactions returns history"

step "Register agent on Identity service"
jws_keygen
ALICE_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

step "Create account via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$ALICE_ID" 0

step "Credit account to create a transaction"
bank_credit_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$ALICE_ID" 50 "bonus-001"
CREDIT_TX_ID=$(echo "$HTTP_BODY" | jq -r '.tx_id')

step "Get transactions using Bearer token"
PAYLOAD=$(jq -nc '{action:"get_transactions"}')
jws_sign "$ALICE_PRIV" "$ALICE_ID" "$PAYLOAD"
http_get_with_bearer "/accounts/$ALICE_ID/transactions" "$JWS_TOKEN"

step "Assert transaction history"
assert_status "200"
assert_json_array_min_length ".transactions" "1"
assert_json_eq ".transactions[0].tx_id" "$CREDIT_TX_ID"
assert_json_eq ".transactions[0].type" "credit"
assert_json_eq ".transactions[0].amount" "50"
assert_json_eq ".transactions[0].balance_after" "50"
assert_json_eq ".transactions[0].reference" "bonus-001"
assert_json_exists ".transactions[0].timestamp"

test_end

