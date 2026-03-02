#!/bin/bash
# test-task-02.sh — TASK-02: Visible feedback appears in task query
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "TASK-02" "Visible feedback appears in task query"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit both directions to reveal feedback"
submit_feedback_pair "$TASK_ID" "$ALICE" "$BOB"

step "Query feedback for task"
http_get "/feedback/task/$TASK_ID"

step "Assert 200 and feedback array has 2 entries with all required fields"
assert_status "200"
assert_json_array_length ".feedback" 2
assert_json_exists ".feedback[0].feedback_id"
assert_json_exists ".feedback[0].task_id"
assert_json_exists ".feedback[0].from_agent_id"
assert_json_exists ".feedback[0].to_agent_id"
assert_json_exists ".feedback[0].category"
assert_json_exists ".feedback[0].rating"
assert_json_exists ".feedback[0].comment"
assert_json_exists ".feedback[0].submitted_at"
assert_json_exists ".feedback[0].visible"
assert_json_exists ".feedback[1].feedback_id"
assert_json_exists ".feedback[1].task_id"
assert_json_exists ".feedback[1].from_agent_id"
assert_json_exists ".feedback[1].to_agent_id"
assert_json_exists ".feedback[1].category"
assert_json_exists ".feedback[1].rating"
assert_json_exists ".feedback[1].comment"
assert_json_exists ".feedback[1].submitted_at"
assert_json_exists ".feedback[1].visible"

test_end
