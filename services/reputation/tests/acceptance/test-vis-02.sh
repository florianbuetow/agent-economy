#!/bin/bash
# test-vis-02.sh — VIS-02: Both directions submitted reveals both
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VIS-02" "Both directions submitted reveals both"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit feedback alice -> bob"
submit_feedback "$TASK_ID" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"

step "Submit feedback bob -> alice"
submit_feedback "$TASK_ID" "$BOB" "$ALICE" "spec_quality" "satisfied" "Clear spec"

step "Query feedback for task"
http_get "/feedback/task/$TASK_ID"

step "Assert 200 and feedback array has exactly 2 entries"
assert_status "200"
assert_json_array_length ".feedback" 2

test_end
