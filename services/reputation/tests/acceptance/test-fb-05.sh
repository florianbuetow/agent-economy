#!/bin/bash
# test-fb-05.sh — FB-05: Submit feedback with empty comment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-05" "Submit feedback with empty comment"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "POST /feedback with comment set to empty string"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    --arg comment "" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating, comment:$comment}')
http_post "/feedback" "$BODY"

step "Assert 201 with empty comment in response"
assert_status "201"
assert_json_eq ".comment" ""

test_end
