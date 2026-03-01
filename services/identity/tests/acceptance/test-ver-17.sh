#!/bin/bash
# test-ver-17.sh — VER-17: Idempotent verification
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-17" "Idempotent verification"

step "Register Alice and build valid verify request body"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"
crypto_sign_raw "$PRIV_A" "idempotent payload"
VERIFY_BODY=$(jq -nc --arg agent_id "$ALICE_ID" --arg payload "$PAYLOAD_B64" --arg signature "$SIGNATURE_B64" '{agent_id:$agent_id, payload:$payload, signature:$signature}')

step "Send first verify request"
http_post "/agents/verify" "$VERIFY_BODY"
assert_status "200"
RESP1="$HTTP_BODY"

step "Send second identical verify request"
http_post "/agents/verify" "$VERIFY_BODY"
assert_status "200"
RESP2="$HTTP_BODY"

step "Assert responses are byte-for-byte identical"
assert_equals "$RESP1" "$RESP2" "responses should be identical"

test_end
