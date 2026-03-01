#!/bin/bash
# test-health-02.sh — HEALTH-02: Total feedback count is exact
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HEALTH-02" "Total feedback count is exact"

step "Read current total_feedback count"
http_get "/health"
BEFORE=$(echo "$HTTP_BODY" | jq -r '.total_feedback')

step "Submit 2 new feedback records"
TASK1=$(gen_task_id)
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)

submit_feedback "$TASK1" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Good work"
assert_status "201"

TASK2=$(gen_task_id)
CAROL=$(gen_agent_id)
DAN=$(gen_agent_id)

submit_feedback "$TASK2" "$CAROL" "$DAN" "spec_quality" "extremely_satisfied" "Clear spec"
assert_status "201"

step "Read new total_feedback count"
http_get "/health"
AFTER=$(echo "$HTTP_BODY" | jq -r '.total_feedback')
EXPECTED=$((BEFORE + 2))

step "Assert count incremented by exactly 2"
assert_equals "$EXPECTED" "$AFTER" "total_feedback should increase by 2"

test_end
