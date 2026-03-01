#!/bin/bash
# test-vis-06.sh — VIS-06: Agent feedback query excludes sealed
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VIS-06" "Agent feedback query excludes sealed"

step "Generate IDs"
TASK_ID="$(gen_task_id)"
ALICE="$(gen_agent_id)"
BOB="$(gen_agent_id)"

step "Submit feedback alice -> bob only (sealed)"
submit_feedback "$TASK_ID" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"

step "Query feedback about bob"
http_get "/feedback/agent/$BOB"

step "Assert 200 and feedback array is empty"
assert_status "200"
assert_json_array_length ".feedback" 0

test_end
