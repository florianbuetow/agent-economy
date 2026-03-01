#!/bin/bash
# test-ver-09.sh — VER-09: Signature too long (128 bytes)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-09" "Signature too long (128 bytes)"

step "Register agent"
crypto_keygen
register_agent "Alice" "$PUBLIC_KEY"
AID="$AGENT_ID"
LONG_SIG=$(crypto_random_b64 128)

step "Verify with long signature"
BODY=$(jq -nc --arg agent_id "$AID" --arg payload "aGVsbG8=" --arg signature "$LONG_SIG" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert invalid signature length error"
assert_status "400"
assert_json_eq ".error" "INVALID_SIGNATURE_LENGTH"

test_end
