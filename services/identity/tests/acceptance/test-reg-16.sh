#!/bin/bash
# test-reg-16.sh — REG-16: Malformed JSON
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-16" "Malformed JSON"

step "Submit truncated malformed JSON"
http_post_raw "/agents/register" '{"name":"Alice","public_key":"ed2'

step "Assert invalid JSON error"
assert_status "400"
assert_json_eq ".error" "INVALID_JSON"

test_end
