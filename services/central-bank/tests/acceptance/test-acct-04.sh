#!/bin/bash
# test-acct-04.sh — ACCT-04: Non-platform agent cannot create accounts
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "ACCT-04" "Non-platform agent cannot create accounts"

step "Register agent on Identity service"
jws_keygen
BOB_PRIV="$PRIVATE_KEY_HEX"
identity_register_agent "Bob" "$PUBLIC_KEY"
BOB_ID="$AGENT_ID"

step "Attempt to create account with non-platform signature"
PAYLOAD=$(jq -nc --arg agent_id "$BOB_ID" --argjson initial_balance 10 '{action:"create_account", agent_id:$agent_id, initial_balance:$initial_balance}')
jws_sign "$BOB_PRIV" "$BOB_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/accounts" "$BODY"

step "Assert forbidden"
assert_status "403"
assert_json_eq ".error" "FORBIDDEN"
assert_error_envelope

test_end

