#!/bin/bash
# test-fb-02.sh — FB-02: Submit valid feedback (spec quality)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-02" "Submit valid feedback (spec quality)"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback: bob->alice, spec_quality, extremely_satisfied, 'Very clear spec'"
submit_feedback "$TASK" "$BOB" "$ALICE" "spec_quality" "extremely_satisfied" "Very clear spec"

step "Assert 201 with correct category and rating"
assert_status "201"
assert_json_eq ".category" "spec_quality"
assert_json_eq ".rating" "extremely_satisfied"

test_end
