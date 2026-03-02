#!/bin/bash
# test-fb-24.sh — FB-24: Unicode characters in comment
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-24" "Unicode characters in comment"

step "Generate IDs"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Submit feedback with Unicode comment (emoji + CJK + accented)"
UNICODE_COMMENT="Great work! 🎉 非常好 très bien"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    --arg rating "extremely_satisfied" \
    --arg comment "$UNICODE_COMMENT" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating, comment:$comment}')
http_post "/feedback" "$BODY"

step "Assert 201 and comment preserved correctly"
assert_status "201"
assert_json_eq ".comment" "$UNICODE_COMMENT"

test_end
