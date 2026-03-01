#!/bin/bash
# test-fb-03.sh — FB-03: Submit feedback without comment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-03" "Submit feedback without comment"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback with comment omitted"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied"

step "Assert 201 with comment null"
assert_status "201"
assert_json_null ".comment"

test_end
