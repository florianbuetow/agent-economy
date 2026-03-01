#!/bin/bash
# test-fb-07.sh — FB-07: Same task, reverse direction is allowed
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-07" "Same task, reverse direction is allowed"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback for (task, alice->bob)"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"
assert_status "201"
FEEDBACK_1=$(echo "$HTTP_BODY" | jq -r '.feedback_id')

step "Submit feedback for (task, bob->alice)"
submit_feedback "$TASK" "$BOB" "$ALICE" "spec_quality" "satisfied" "Clear spec"
assert_status "201"
FEEDBACK_2=$(echo "$HTTP_BODY" | jq -r '.feedback_id')

step "Assert both returned 201 with different feedback_ids"
assert_not_equals "$FEEDBACK_1" "$FEEDBACK_2" "feedback_ids are different"

test_end
