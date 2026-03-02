#!/bin/bash
# test-fb-23.sh — FB-23: Duplicate with different rating still rejected
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-23" "Duplicate with different rating still rejected"

step "Generate IDs"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback with rating 'satisfied'"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "First"
assert_status "201"

step "Submit same (task, from, to) with different rating and category"
submit_feedback "$TASK" "$ALICE" "$BOB" "spec_quality" "extremely_satisfied" "Second"
assert_status "409"
assert_json_eq ".error" "FEEDBACK_EXISTS"

test_end
