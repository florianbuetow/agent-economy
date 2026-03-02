#!/bin/bash
# test-fb-21.sh — FB-21: All three rating values are accepted
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-21" "All three rating values are accepted"

ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)

step "Submit feedback with rating 'dissatisfied'"
TASK_1=$(gen_task_id)
submit_feedback "$TASK_1" "$ALICE" "$BOB" "delivery_quality" "dissatisfied" "Poor work"
assert_status "201"
assert_json_eq ".rating" "dissatisfied"

step "Submit feedback with rating 'satisfied'"
TASK_2=$(gen_task_id)
submit_feedback "$TASK_2" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"
assert_status "201"
assert_json_eq ".rating" "satisfied"

step "Submit feedback with rating 'extremely_satisfied'"
TASK_3=$(gen_task_id)
submit_feedback "$TASK_3" "$ALICE" "$BOB" "delivery_quality" "extremely_satisfied" "Excellent work"
assert_status "201"
assert_json_eq ".rating" "extremely_satisfied"

test_end
