#!/bin/bash
# test-ver-13.sh — VER-13: Empty payload is supported
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-13" "Empty payload is supported"

step "Register Alice and sign empty payload"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"
crypto_sign_raw "$PRIV_A" ""

step "Verify empty payload signature"
BODY=$(jq -nc --arg agent_id "$ALICE_ID" --arg payload "$PAYLOAD_B64" --arg signature "$SIGNATURE_B64" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert verification is valid"
assert_status "200"
assert_json_true ".valid"

test_end
