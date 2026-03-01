#!/bin/bash
# test-ver-03.sh — VER-03: Tampered payload
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-03" "Tampered payload"

step "Register Alice and sign original payload"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"
crypto_sign_raw "$PRIV_A" "original message"
ORIG_SIG="$SIGNATURE_B64"

step "Create tampered payload and verify with original signature"
crypto_sign_raw "$PRIV_A" "tampered message"
TAMPERED_PAYLOAD="$PAYLOAD_B64"
BODY=$(jq -nc --arg agent_id "$ALICE_ID" --arg payload "$TAMPERED_PAYLOAD" --arg signature "$ORIG_SIG" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert signature mismatch"
assert_status "200"
assert_json_false ".valid"
assert_json_eq ".reason" "signature mismatch"

test_end
