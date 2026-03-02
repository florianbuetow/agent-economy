#!/bin/bash
# test-ver-12.sh — VER-12: Wrong field types
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-12" "Wrong field types"

step "Submit wrong JSON types"
http_post "/agents/verify" '{"agent_id":true,"payload":[1],"signature":{"x":1}}'

step "Assert invalid field type error"
assert_status "400"
assert_json_eq ".error" "INVALID_FIELD_TYPE"

test_end
