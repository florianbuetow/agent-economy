#!/bin/bash
# test-reg-14.sh — REG-14: All-zero key
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-14" "All-zero key"

step "Generate all-zero 32-byte public key"
crypto_zero_key

step "Submit registration with all-zero key"
BODY=$(jq -nc --arg name "Alice" --arg public_key "$PUBLIC_KEY" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY"

step "Assert invalid public key error"
assert_status "400"
assert_json_eq ".error" "INVALID_PUBLIC_KEY"

test_end
