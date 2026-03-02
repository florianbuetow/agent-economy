#!/bin/bash
# test-vis-08.sh — VIS-08: Revealing does not affect other tasks
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VIS-08" "Revealing does not affect other tasks"

step "Generate IDs"
TASK_1="$(gen_task_id)"
TASK_2="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit both directions for task_1 (revealed)"
submit_feedback_pair "$TASK_1" "$ALICE" "$BOB"

step "Submit only alice -> bob for task_2 (sealed)"
submit_feedback "$TASK_2" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"

step "Query feedback for task_2"
http_get "/feedback/task/$TASK_2"

step "Assert 200 and feedback array is empty (task_2 still sealed)"
assert_status "200"
assert_json_array_length ".feedback" 0

test_end
