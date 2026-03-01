#!/bin/bash
# test-fb-14.sh — FB-14: Missing required fields (one at a time)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-14" "Missing required fields (one at a time)"

ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit without task_id"
BODY=$(jq -nc \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

step "Submit without from_agent_id"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{task_id:$task_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

step "Submit without to_agent_id"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

step "Submit without category"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg rating "satisfied" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, rating:$rating}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

step "Submit without rating"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category}')
http_post "/feedback" "$BODY"
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"

test_end
