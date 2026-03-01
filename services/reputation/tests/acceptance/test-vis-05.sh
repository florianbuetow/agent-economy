#!/bin/bash
# test-vis-05.sh — VIS-05: Revealed feedback returns 200 on direct lookup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VIS-05" "Revealed feedback returns 200 on direct lookup"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit both directions to reveal feedback"
submit_feedback_pair "$TASK_ID" "$ALICE" "$BOB"

step "Lookup revealed feedback by ID"
http_get "/feedback/$FEEDBACK_ID_1"

step "Assert 200 and all fields present"
assert_status "200"
assert_json_exists ".feedback_id"
assert_json_exists ".task_id"
assert_json_exists ".from_agent_id"
assert_json_exists ".to_agent_id"
assert_json_exists ".category"
assert_json_exists ".rating"
assert_json_exists ".comment"
assert_json_exists ".submitted_at"
assert_json_true ".visible"

test_end
