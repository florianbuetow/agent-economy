#!/bin/bash
# test-fb-09.sh — FB-09: Self-feedback is rejected
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-09" "Self-feedback is rejected"

step "Generate one agent ID and a task ID"
ALICE=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback where from_agent_id equals to_agent_id"
submit_feedback "$TASK" "$ALICE" "$ALICE" "delivery_quality" "satisfied" "Self review"

step "Assert 400 with SELF_FEEDBACK error"
assert_status "400"
assert_json_eq ".error" "SELF_FEEDBACK"

test_end
