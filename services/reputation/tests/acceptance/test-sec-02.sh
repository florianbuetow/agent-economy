#!/bin/bash
# test-sec-02.sh — SEC-02: No internal error leakage
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "SEC-02" "No internal error leakage"

FORBIDDEN=("traceback" "Traceback" "File \"" "sqlalchemy" "sqlite" "psycopg" ".py" "/home/" "/Users/" "/app/src/" "DETAIL:" "Internal Server Error" "NoneType")

step "Trigger INVALID_JSON and check leakage"
http_post_raw "/feedback" '{broken'
assert_body_not_contains "${FORBIDDEN[@]}"

step "Trigger SELF_FEEDBACK and check leakage"
SELF_AGENT=$(gen_agent_id)
TASK=$(gen_task_id)
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$SELF_AGENT" \
    --arg to_agent_id "$SELF_AGENT" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating}')
http_post "/feedback" "$BODY"
assert_body_not_contains "${FORBIDDEN[@]}"

step "Trigger FEEDBACK_NOT_FOUND and check leakage"
http_get "/feedback/fb-00000000-0000-0000-0000-000000000000"
assert_body_not_contains "${FORBIDDEN[@]}"

step "Trigger malformed ID and check leakage"
http_get "/feedback/not-a-valid-id"
assert_body_not_contains "${FORBIDDEN[@]}"

test_end
