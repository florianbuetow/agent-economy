#!/bin/bash
# test-agent-03.sh — AGENT-03: Feedback BY agent not included
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "AGENT-03" "Feedback BY agent not included"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit and reveal feedback for task (alice -> bob and bob -> alice)"
submit_feedback_pair "$TASK_ID" "$ALICE" "$BOB"

step "Query feedback about bob"
http_get "/feedback/agent/$BOB"

step "Assert 200 and feedback array has exactly 1 entry (alice's about bob)"
assert_status "200"
assert_json_array_length ".feedback" 1
assert_json_eq ".feedback[0].from_agent_id" "$ALICE"
assert_json_eq ".feedback[0].to_agent_id" "$BOB"

test_end
