#!/bin/bash
# test-task-01.sh — TASK-01: No feedback for task
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "TASK-01" "No feedback for task"

step "Generate random task ID"
TASK_ID="$(gen_task_id)"

step "Query feedback for task with no feedback"
http_get "/feedback/task/$TASK_ID"

step "Assert 200, task_id matches, feedback is empty array"
assert_status "200"
assert_json_eq ".task_id" "$TASK_ID"
assert_json_array_length ".feedback" 0

test_end
