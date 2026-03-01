#!/bin/bash
# test-escrow-03.sh — ESCROW-03: Agent cannot lock another agent's funds
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ESCROW-03" "Agent cannot lock another agent's funds"

step "Register agents on Identity service"
jws_keygen
ALICE_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

jws_keygen
BOB_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Bob" "$PUBLIC_KEY"
BOB_ID="$AGENT_ID"

step "Bob attempts to lock Alice's funds"
PAYLOAD=$(jq -nc --arg agent_id "$ALICE_ID" --arg task_id "T-003" --argjson amount 10 '{action:"escrow_lock", agent_id:$agent_id, amount:$amount, task_id:$task_id}')
jws_sign "$BOB_PRIV" "$BOB_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/lock" "$BODY"

step "Assert forbidden"
assert_status "403"
assert_json_eq ".error" "FORBIDDEN"
assert_error_envelope

test_end

