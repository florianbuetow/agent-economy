#!/bin/bash
# test-reg-07.sh — REG-07: Missing public_key
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-07" "Missing public_key"

step "Submit registration without public_key"
http_post "/agents/register" '{"name":"Orphan"}'

step "Assert missing field error"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

test_end
