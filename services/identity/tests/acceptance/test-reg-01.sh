#!/bin/bash
# test-reg-01.sh — REG-01: Register one valid agent
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-01" "Register one valid agent"

step "Generate a valid Ed25519 keypair"
crypto_keygen

step "Register Alice with the generated public key"
BODY=$(jq -nc --arg name "Alice" --arg public_key "$PUBLIC_KEY" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY"

step "Assert successful registration response"
assert_status "201"
assert_json_exists ".agent_id"
assert_json_exists ".name"
assert_json_exists ".public_key"
assert_json_exists ".registered_at"
assert_json_eq ".name" "Alice"
assert_json_eq ".public_key" "$PUBLIC_KEY"
assert_json_matches ".agent_id" "$UUID4_PATTERN"
assert_json_matches ".registered_at" "$ISO8601_PATTERN"

test_end
