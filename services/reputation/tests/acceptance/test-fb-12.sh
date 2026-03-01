#!/bin/bash
# test-fb-12.sh — FB-12: Invalid rating value
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-12" "Invalid rating value"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback with invalid rating 'excellent'"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "excellent" "Good work"

step "Assert 400 with INVALID_RATING error"
assert_status "400"
assert_json_eq ".error" "INVALID_RATING"

test_end
