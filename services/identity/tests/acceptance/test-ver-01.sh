#!/bin/bash
# test-ver-01.sh — VER-01: Valid signature verifies true
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-01" "Valid signature verifies true"

step "Register Alice and produce a valid signature"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"
crypto_sign_raw "$PRIV_A" "hello world"

step "Verify signature"
BODY=$(jq -nc --arg agent_id "$ALICE_ID" --arg payload "$PAYLOAD_B64" --arg signature "$SIGNATURE_B64" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert valid verification"
assert_status "200"
assert_json_true ".valid"
assert_json_eq ".agent_id" "$ALICE_ID"

test_end
