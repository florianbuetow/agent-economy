#!/bin/bash
# test-agent-02.sh — AGENT-02: Feedback about agent from multiple tasks
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "AGENT-02" "Feedback about agent from multiple tasks"

step "Generate IDs"
TASK_1="$(gen_task_id)"
TASK_2="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"
CAROL="$(gen_agent_id)"

step "Submit and reveal feedback for task_1 (alice -> bob and bob -> alice)"
submit_feedback_pair "$TASK_1" "$ALICE" "$BOB"

step "Submit and reveal feedback for task_2 (carol -> bob and bob -> carol)"
submit_feedback_pair "$TASK_2" "$CAROL" "$BOB"

step "Query feedback about bob"
http_get "/feedback/agent/$BOB"

step "Assert 200 and feedback array has exactly 2 entries"
assert_status "200"
assert_json_array_length ".feedback" 2

test_end
