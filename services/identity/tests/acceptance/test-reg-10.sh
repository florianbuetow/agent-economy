#!/bin/bash
# test-reg-10.sh — REG-10: Empty or whitespace-only name
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-10" "Empty or whitespace-only name"

step "Submit empty name"
crypto_keygen
PUB1="$PUBLIC_KEY"
BODY_EMPTY=$(jq -nc --arg name "" --arg public_key "$PUB1" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY_EMPTY"
assert_status "400"
assert_json_eq ".error" "INVALID_NAME"

step "Submit whitespace-only name"
crypto_keygen
PUB2="$PUBLIC_KEY"
BODY_SPACES=$(jq -nc --arg name "   " --arg public_key "$PUB2" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY_SPACES"
assert_status "400"
assert_json_eq ".error" "INVALID_NAME"

test_end
