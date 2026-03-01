#!/bin/bash
# test-vis-03.sh — VIS-03: Second submission returns visible=true
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VIS-03" "Second submission returns visible=true"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit feedback alice -> bob"
submit_feedback "$TASK_ID" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"

step "Assert visible is false on first submission"
assert_status "201"
assert_json_false ".visible"

step "Submit feedback bob -> alice"
submit_feedback "$TASK_ID" "$BOB" "$ALICE" "spec_quality" "satisfied" "Clear spec"

step "Assert visible is true on second submission"
assert_status "201"
assert_json_true ".visible"

test_end
