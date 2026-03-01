#!/bin/bash
# test-ver-10.sh — VER-10: Missing required fields
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-10" "Missing required fields"

step "Prepare valid registered agent and signature payload"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
register_agent "Alice" "$PUBLIC_KEY"
AID="$AGENT_ID"
crypto_sign_raw "$PRIV_A" "hello world"

step "Omit agent_id"
BODY_NO_AGENT=$(jq -nc --arg payload "$PAYLOAD_B64" --arg signature "$SIGNATURE_B64" '{payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY_NO_AGENT"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

step "Omit payload"
BODY_NO_PAYLOAD=$(jq -nc --arg agent_id "$AID" --arg signature "$SIGNATURE_B64" '{agent_id:$agent_id, signature:$signature}')
http_post "/agents/verify" "$BODY_NO_PAYLOAD"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

step "Omit signature"
BODY_NO_SIGNATURE=$(jq -nc --arg agent_id "$AID" --arg payload "$PAYLOAD_B64" '{agent_id:$agent_id, payload:$payload}')
http_post "/agents/verify" "$BODY_NO_SIGNATURE"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

test_end
