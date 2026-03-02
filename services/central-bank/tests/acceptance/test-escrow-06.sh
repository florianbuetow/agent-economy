#!/bin/bash
# test-escrow-06.sh — ESCROW-06: Release non-existent escrow returns 404
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ESCROW-06" "Release non-existent escrow returns 404"

step "Register recipient agent on Identity service"
jws_keygen
identity_register_agent "Recipient" "$PUBLIC_KEY"
RECIPIENT_ID="$AGENT_ID"

step "Create recipient account via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$RECIPIENT_ID" 0

step "Release non-existent escrow using platform token"
ESCROW_ID="esc-fake"
PAYLOAD=$(jq -nc --arg escrow_id "$ESCROW_ID" --arg recipient "$RECIPIENT_ID" '{action:"escrow_release", escrow_id:$escrow_id, recipient_account_id:$recipient}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/$ESCROW_ID/release" "$BODY"

step "Assert escrow not found"
assert_status "404"
assert_json_eq ".error" "ESCROW_NOT_FOUND"
assert_error_envelope

test_end

