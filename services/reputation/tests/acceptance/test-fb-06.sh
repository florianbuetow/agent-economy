#!/bin/bash
# test-fb-06.sh — FB-06: Duplicate feedback is rejected
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-06" "Duplicate feedback is rejected"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback for (task, alice, bob)"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"
assert_status "201"

step "Submit identical feedback again"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"

step "Assert 409 with FEEDBACK_EXISTS error"
assert_status "409"
assert_json_eq ".error" "FEEDBACK_EXISTS"

test_end
