#!/bin/bash
# test-reg-06.sh — REG-06: Missing name
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-06" "Missing name"

step "Generate a valid public key"
crypto_keygen

step "Submit registration without name"
BODY=$(jq -nc --arg public_key "$PUBLIC_KEY" '{public_key:$public_key}')
http_post "/agents/register" "$BODY"

step "Assert missing field error"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

test_end
