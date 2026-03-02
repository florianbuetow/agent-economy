#!/bin/bash
# test-reg-11.sh — REG-11: Invalid key prefix
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-11" "Invalid key prefix"

step "Submit registration with non-ed25519 key prefix"
http_post "/agents/register" '{"name":"Alice","public_key":"rsa:AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA="}'

step "Assert invalid public key error"
assert_status "400"
assert_json_eq ".error" "INVALID_PUBLIC_KEY"

test_end
