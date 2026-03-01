#!/bin/bash
# test-fb-15.sh — FB-15: Null required fields
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-15" "Null required fields"

step "Submit with all required fields set to null"
BODY='{"task_id":null,"from_agent_id":null,"to_agent_id":null,"category":null,"rating":null}'
http_post "/feedback" "$BODY"

step "Assert 400 with MISSING_FIELD error"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

test_end
