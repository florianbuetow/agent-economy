#!/bin/bash
# test-fb-10.sh — FB-10: Comment exceeding max length is rejected
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-10" "Comment exceeding max length is rejected"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Generate a 257-character comment"
LONG_COMMENT=$(python3 -c "print('A' * 257)")

step "Submit feedback with oversized comment"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "$LONG_COMMENT"

step "Assert 400 with COMMENT_TOO_LONG error"
assert_status "400"
assert_json_eq ".error" "COMMENT_TOO_LONG"

test_end
