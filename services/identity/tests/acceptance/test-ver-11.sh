#!/bin/bash
# test-ver-11.sh — VER-11: Null required fields
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-11" "Null required fields"

step "Submit null values for required fields"
http_post "/agents/verify" '{"agent_id":null,"payload":null,"signature":null}'

step "Assert missing field error"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

test_end
