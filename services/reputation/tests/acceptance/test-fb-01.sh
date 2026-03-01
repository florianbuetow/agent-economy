#!/bin/bash
# test-fb-01.sh — FB-01: Submit valid feedback (delivery quality)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-01" "Submit valid feedback (delivery quality)"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback: alice->bob, delivery_quality, satisfied, 'Good work'"
submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"

step "Assert 201 with all fields present and correct"
assert_status "201"
assert_json_exists ".feedback_id"
assert_json_exists ".task_id"
assert_json_exists ".from_agent_id"
assert_json_exists ".to_agent_id"
assert_json_exists ".category"
assert_json_exists ".rating"
assert_json_exists ".submitted_at"
assert_json_matches ".feedback_id" "$FEEDBACK_UUID4_PATTERN"
assert_json_matches ".submitted_at" "$ISO8601_PATTERN"
assert_json_false ".visible"
assert_json_eq ".task_id" "$TASK"
assert_json_eq ".from_agent_id" "$ALICE"
assert_json_eq ".to_agent_id" "$BOB"
assert_json_eq ".category" "delivery_quality"
assert_json_eq ".rating" "satisfied"
assert_json_eq ".comment" "Good work"

test_end
