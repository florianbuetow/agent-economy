#!/bin/bash
# test-fb-11.sh — FB-11: Comment at exactly max length is accepted
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-11" "Comment at exactly max length is accepted"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Generate a 256-character comment"
MAX_COMMENT=$(python3 -c "print('A' * 256)")

step "Submit feedback with max-length comment"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "$MAX_COMMENT"

step "Assert 201"
assert_status "201"

test_end
