#!/bin/bash
# test-vis-07.sh — VIS-07: Agent feedback query includes revealed
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VIS-07" "Agent feedback query includes revealed"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit feedback alice -> bob"
submit_feedback "$TASK_ID" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"

step "Submit feedback bob -> alice (reveals both)"
submit_feedback "$TASK_ID" "$BOB" "$ALICE" "spec_quality" "satisfied" "Clear spec"

step "Query feedback about bob"
http_get "/feedback/agent/$BOB"

step "Assert 200 and feedback array has 1 entry (alice's about bob)"
assert_status "200"
assert_json_array_length ".feedback" 1

test_end
