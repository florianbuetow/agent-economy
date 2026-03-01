#!/bin/bash
# test-vis-04.sh — VIS-04: Sealed feedback returns 404 on direct lookup
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VIS-04" "Sealed feedback returns 404 on direct lookup"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit feedback alice -> bob only"
submit_feedback "$TASK_ID" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"
FEEDBACK_ID=$(echo "$HTTP_BODY" | jq -r '.feedback_id')

step "Lookup sealed feedback by ID"
http_get "/feedback/$FEEDBACK_ID"

step "Assert 404 with FEEDBACK_NOT_FOUND error"
assert_status "404"
assert_json_eq ".error" "FEEDBACK_NOT_FOUND"

test_end
