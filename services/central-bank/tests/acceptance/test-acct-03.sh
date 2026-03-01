#!/bin/bash
# test-acct-03.sh — ACCT-03: Duplicate account returns 409 ACCOUNT_EXISTS
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-03" "Duplicate account returns 409 ACCOUNT_EXISTS"

step "Register agent on Identity service"
jws_keygen
identity_register_agent "DupCase" "$PUBLIC_KEY"
AGENT_ID_DUP="$AGENT_ID"

step "Create account via platform token"
bank_create_account "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$AGENT_ID_DUP" 0
assert_status "201"

step "Create same account again"
PAYLOAD=$(jq -nc --arg agent_id "$AGENT_ID_DUP" --argjson initial_balance 0 '{action:"create_account", agent_id:$agent_id, initial_balance:$initial_balance}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/accounts" "$BODY"

step "Assert conflict"
assert_status "409"
assert_json_eq ".error" "ACCOUNT_EXISTS"
assert_error_envelope

test_end

