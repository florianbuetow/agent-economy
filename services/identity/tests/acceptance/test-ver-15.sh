#!/bin/bash
# test-ver-15.sh — VER-15: Malformed JSON
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-15" "Malformed JSON"

step "Submit malformed JSON"
http_post_raw "/agents/verify" '{"agent_id":"abc","payload":'

step "Assert invalid JSON error"
assert_status "400"
assert_json_eq ".error" "INVALID_JSON"

test_end
