#!/bin/bash
# test-read-05.sh — READ-05: Idempotent read returns identical response
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "READ-05" "Idempotent read returns identical response"

step "Generate IDs and submit feedback pair to reveal"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"
submit_feedback_pair "$TASK_ID" "$ALICE" "$BOB"
FEEDBACK_ID="$FEEDBACK_ID_1"

step "First read"
http_get "/feedback/$FEEDBACK_ID"
assert_status "200"
BODY_1="$HTTP_BODY"

step "Second read"
http_get "/feedback/$FEEDBACK_ID"
assert_status "200"
BODY_2="$HTTP_BODY"

step "Assert both responses are identical"
assert_equals "$BODY_1" "$BODY_2" "Both reads return identical JSON"

test_end
