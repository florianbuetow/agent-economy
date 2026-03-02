#!/bin/bash
# test-ver-04.sh — VER-04: Cross-identity replay
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-04" "Cross-identity replay"

step "Register Alice and Eve"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"
crypto_keygen
register_agent "Eve" "$PUBLIC_KEY"
EVE_ID="$AGENT_ID"

step "Sign with Alice but verify under Eve"
crypto_sign_raw "$PRIV_A" "secret action"
BODY=$(jq -nc --arg agent_id "$EVE_ID" --arg payload "$PAYLOAD_B64" --arg signature "$SIGNATURE_B64" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert signature mismatch"
assert_status "200"
assert_json_false ".valid"
assert_json_eq ".reason" "signature mismatch"

test_end
