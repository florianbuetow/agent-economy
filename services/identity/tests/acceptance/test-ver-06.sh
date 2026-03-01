#!/bin/bash
# test-ver-06.sh — VER-06: Invalid base64 payload
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-06" "Invalid base64 payload"

step "Register agent"
crypto_keygen
register_agent "Alice" "$PUBLIC_KEY"
AID="$AGENT_ID"
FAKE_SIG=$(crypto_random_b64 64)

step "Verify with invalid payload base64"
BODY=$(jq -nc --arg agent_id "$AID" --arg payload "%%%not-base64%%%" --arg signature "$FAKE_SIG" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert invalid base64 error"
assert_status "400"
assert_json_eq ".error" "INVALID_BASE64"

test_end
