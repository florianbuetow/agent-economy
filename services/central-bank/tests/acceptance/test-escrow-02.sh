#!/bin/bash
# test-escrow-02.sh — ESCROW-02: Insufficient funds returns 402
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ESCROW-02" "Insufficient funds returns 402"

step "Register agent on Identity service"
jws_keygen
PAYER_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Payer" "$PUBLIC_KEY"
PAYER_ID="$AGENT_ID"

step "Create payer account with low balance"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYER_ID" 10

step "Attempt escrow lock beyond balance"
PAYLOAD=$(jq -nc --arg agent_id "$PAYER_ID" --arg task_id "T-002" --argjson amount 50 '{action:"escrow_lock", agent_id:$agent_id, amount:$amount, task_id:$task_id}')
jws_sign "$PAYER_PRIV" "$PAYER_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/lock" "$BODY"

step "Assert insufficient funds"
assert_status "402"
assert_json_eq ".error" "INSUFFICIENT_FUNDS"
assert_error_envelope

test_end

