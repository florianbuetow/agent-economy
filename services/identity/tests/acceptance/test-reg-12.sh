#!/bin/bash
# test-reg-12.sh — REG-12: Invalid base64 in key
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-12" "Invalid base64 in key"

step "Submit registration with invalid base64 public key"
http_post "/agents/register" '{"name":"Alice","public_key":"ed25519:%%%not-base64%%%"}'

step "Assert invalid public key error"
assert_status "400"
assert_json_eq ".error" "INVALID_PUBLIC_KEY"

test_end
