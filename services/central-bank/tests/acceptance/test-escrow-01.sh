#!/bin/bash
# test-escrow-01.sh — ESCROW-01: Lock funds in escrow success
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ESCROW-01" "Lock funds in escrow success"

step "Register agent on Identity service"
jws_keygen
PAYER_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Payer" "$PUBLIC_KEY"
PAYER_ID="$AGENT_ID"

step "Create payer account via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYER_ID" 100

step "Lock funds in escrow with agent signature"
PAYLOAD=$(jq -nc --arg agent_id "$PAYER_ID" --arg task_id "T-001" --argjson amount 50 '{action:"escrow_lock", agent_id:$agent_id, amount:$amount, task_id:$task_id}')
jws_sign "$PAYER_PRIV" "$PAYER_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/lock" "$BODY"

step "Assert escrow lock response"
assert_status "201"
assert_json_matches ".escrow_id" "$ESCROW_ID_PATTERN"
assert_json_eq ".amount" "50"
assert_json_eq ".task_id" "T-001"
assert_json_eq ".status" "locked"

test_end

