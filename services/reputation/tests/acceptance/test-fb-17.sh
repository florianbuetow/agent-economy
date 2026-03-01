#!/bin/bash
# test-fb-17.sh — FB-17: Malformed JSON body
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-17" "Malformed JSON body"

step "Send truncated JSON via http_post_raw"
http_post_raw "/feedback" '{"task_id": "abc", "from_agent_id":'

step "Assert 400 with INVALID_JSON error"
assert_status "400"
assert_json_eq ".error" "INVALID_JSON"

test_end
