#!/bin/bash
# test-fb-13.sh — FB-13: Invalid category value
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-13" "Invalid category value"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback with invalid category 'timeliness'"
submit_feedback "$TASK" "$ALICE" "$BOB" "timeliness" "satisfied" "On time"

step "Assert 400 with INVALID_CATEGORY error"
assert_status "400"
assert_json_eq ".error" "INVALID_CATEGORY"

test_end
