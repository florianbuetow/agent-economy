#!/bin/bash
# test-fb-25.sh — FB-25: Empty string agent IDs rejected
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-25" "Empty string agent IDs rejected"

TASK=$(gen_task_id)
ALICE=$(gen_agent_id)

step "Submit with empty from_agent_id"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "" \
    --arg to_agent_id "$ALICE" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

step "Submit with empty to_agent_id"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

step "Submit with empty task_id"
BODY=$(jq -nc \
    --arg task_id "" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$(gen_agent_id)" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

test_end
