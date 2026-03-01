#!/bin/bash
# test-ver-14.sh — VER-14: Large payload (1 MB)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-14" "Large payload (1 MB)"

step "Register Alice and sign a 1MB payload"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"
crypto_large_sign "$PRIV_A" 1048576

step "Verify large payload signature"
build_verify_body "$ALICE_ID" "$PAYLOAD_B64" "$SIGNATURE_B64"
http_post_file "/agents/verify" "$VERIFY_BODY_FILE"
rm -f "$VERIFY_BODY_FILE"

step "Assert verification is valid"
assert_status "200"
assert_json_true ".valid"

test_end
