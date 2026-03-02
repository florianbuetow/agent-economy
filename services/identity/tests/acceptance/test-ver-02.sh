#!/bin/bash
# test-ver-02.sh — VER-02: Wrong signature (Bob signs, verify under Alice)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-02" "Wrong signature (Bob signs, verify under Alice)"

step "Register Alice and Bob with distinct keys"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"
crypto_keygen
PRIV_B="$PRIVATE_KEY_HEX"
register_agent "Bob" "$PUBLIC_KEY"

step "Sign payload with Bob and verify under Alice"
crypto_sign_raw "$PRIV_B" "hello world"
BODY=$(jq -nc --arg agent_id "$ALICE_ID" --arg payload "$PAYLOAD_B64" --arg signature "$SIGNATURE_B64" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert signature mismatch response"
assert_status "200"
assert_json_false ".valid"
assert_json_eq ".reason" "signature mismatch"

test_end
