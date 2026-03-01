#!/bin/bash
# test-reg-08.sh — REG-08: Null required fields
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-08" "Null required fields"

step "Submit null values for required fields"
http_post "/agents/register" '{"name":null,"public_key":null}'

step "Assert missing field error"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

test_end
