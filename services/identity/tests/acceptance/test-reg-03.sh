#!/bin/bash
# test-reg-03.sh — REG-03: Duplicate key is rejected
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-03" "Duplicate key is rejected"

step "Register Alice with a new key"
crypto_keygen
PUB_A="$PUBLIC_KEY"
register_agent "Alice" "$PUB_A"
ALICE_ID="$AGENT_ID"

step "Attempt to register Eve with the same public key"
BODY=$(jq -nc --arg name "Eve" --arg public_key "$PUB_A" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY"
assert_status "409"
assert_json_eq ".error" "PUBLIC_KEY_EXISTS"

step "Verify original Alice record is unchanged"
http_get "/agents/$ALICE_ID"
assert_status "200"
assert_json_eq ".name" "Alice"
assert_json_eq ".public_key" "$PUB_A"

test_end
