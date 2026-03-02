#!/bin/bash
# test-acct-10.sh — ACCT-10: Create account for non-existent agent returns 404 AGENT_NOT_FOUND
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-10" "Create account for non-existent agent returns 404 AGENT_NOT_FOUND"

step "Create account for unknown agent via platform token"
UNKNOWN_AGENT_ID="a-00000000-0000-0000-0000-000000000000"
PAYLOAD=$(jq -nc --arg agent_id "$UNKNOWN_AGENT_ID" --argjson initial_balance 0 '{action:"create_account", agent_id:$agent_id, initial_balance:$initial_balance}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/accounts" "$BODY"

step "Assert agent not found"
assert_status "404"
assert_json_eq ".error" "AGENT_NOT_FOUND"
assert_error_envelope

test_end

