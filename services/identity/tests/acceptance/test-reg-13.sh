#!/bin/bash
# test-reg-13.sh — REG-13: Wrong key length (16 bytes)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-13" "Wrong key length (16 bytes)"

step "Generate a 16-byte public key payload"
crypto_pubkey_bytes 16

step "Submit registration with invalid key length"
BODY=$(jq -nc --arg name "Alice" --arg public_key "$PUBLIC_KEY" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY"

step "Assert invalid public key error"
assert_status "400"
assert_json_eq ".error" "INVALID_PUBLIC_KEY"

test_end
