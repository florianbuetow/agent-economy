#!/bin/bash
# test-fb-19.sh — FB-19: Mass-assignment resistance (extra fields)
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-19" "Mass-assignment resistance (extra fields)"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Send feedback with extra fields: feedback_id, submitted_at, visible, is_admin"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    --arg comment "Good work" \
    --arg feedback_id "fb-injected-id" \
    --arg submitted_at "1999-01-01T00:00:00Z" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating, comment:$comment, feedback_id:$feedback_id, submitted_at:$submitted_at, visible:true, is_admin:true}')
http_post "/feedback" "$BODY"

step "Assert 201 with service-generated values, extra fields ignored"
assert_status "201"
assert_json_matches ".feedback_id" "$FEEDBACK_UUID4_PATTERN"
assert_not_equals "fb-injected-id" "$(echo "$HTTP_BODY" | jq -r '.feedback_id')" "feedback_id is service-generated, not injected"
assert_not_equals "1999-01-01T00:00:00Z" "$(echo "$HTTP_BODY" | jq -r '.submitted_at')" "submitted_at is service-generated, not injected"
assert_json_not_exists ".is_admin"

test_end
