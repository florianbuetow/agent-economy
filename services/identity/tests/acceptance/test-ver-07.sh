#!/bin/bash
# test-ver-07.sh — VER-07: Invalid base64 signature
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-07" "Invalid base64 signature"

step "Register agent"
crypto_keygen
register_agent "Alice" "$PUBLIC_KEY"
AID="$AGENT_ID"

step "Verify with invalid signature base64"
BODY=$(jq -nc --arg agent_id "$AID" --arg payload "aGVsbG8=" --arg signature "%%%not-base64%%%" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert invalid base64 error"
assert_status "400"
assert_json_eq ".error" "INVALID_BASE64"

test_end
