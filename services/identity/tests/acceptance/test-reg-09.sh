#!/bin/bash
# test-reg-09.sh — REG-09: Wrong field types
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-09" "Wrong field types"

step "Submit wrong JSON types for required fields"
http_post "/agents/register" '{"name":123,"public_key":true}'

step "Assert invalid field type error"
assert_status "400"
assert_json_eq ".error" "INVALID_FIELD_TYPE"

test_end
