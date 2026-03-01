#!/bin/bash
# test-vis-01.sh — VIS-01: Single-direction feedback is sealed
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VIS-01" "Single-direction feedback is sealed"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit feedback alice -> bob only"
submit_feedback "$TASK_ID" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"

step "Query feedback for task"
http_get "/feedback/task/$TASK_ID"

step "Assert 200 and feedback array is empty (sealed)"
assert_status "200"
assert_json_array_length ".feedback" 0

test_end
