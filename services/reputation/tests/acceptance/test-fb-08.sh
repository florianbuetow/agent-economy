#!/bin/bash
# test-fb-08.sh — FB-08: Same agents, different task is allowed
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-08" "Same agents, different task is allowed"

step "Generate two agent IDs and two task IDs"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK_1=$(gen_task_id)
TASK_2=$(gen_task_id)

step "Submit feedback for (task_1, alice->bob)"
submit_feedback "$TASK_1" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"
assert_status "201"
FEEDBACK_1=$(echo "$HTTP_BODY" | jq -r '.feedback_id')

step "Submit feedback for (task_2, alice->bob)"
submit_feedback "$TASK_2" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work again"
assert_status "201"
FEEDBACK_2=$(echo "$HTTP_BODY" | jq -r '.feedback_id')

step "Assert both returned 201 with different feedback_ids"
assert_not_equals "$FEEDBACK_1" "$FEEDBACK_2" "feedback_ids are different"

test_end
