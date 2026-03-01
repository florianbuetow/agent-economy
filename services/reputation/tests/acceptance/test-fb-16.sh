#!/bin/bash
# test-fb-16.sh — FB-16: Wrong field types
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-16" "Wrong field types"

step "Submit with wrong types: task_id:123, from_agent_id:true, to_agent_id:[], category:42, rating:{}"
BODY='{"task_id":123,"from_agent_id":true,"to_agent_id":[],"category":42,"rating":{}}'
http_post "/feedback" "$BODY"

step "Assert 400 with INVALID_FIELD_TYPE error"
assert_status "400"
assert_json_eq ".error" "INVALID_FIELD_TYPE"

test_end
