#!/bin/bash
# test-sec-03.sh — SEC-03: Feedback IDs are opaque and random-format
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "SEC-03" "Feedback IDs are opaque and random-format"

step "Submit 5 feedback records and collect IDs"
IDS=()
for i in 1 2 3 4 5; do
    TASK=$(gen_task_id)
    ALICE=$(gen_agent_id)
    BOB=$(gen_agent_id)
    submit_feedback "$TASK" "$ALICE" "$BOB" "delivery_quality" "satisfied" "Feedback $i"
    assert_status "201"
    FB_ID=$(echo "$HTTP_BODY" | jq -r '.feedback_id')
    IDS+=("$FB_ID")
done

step "Assert every feedback_id matches fb-<uuid4> pattern"
for id in "${IDS[@]}"; do
    if [[ "$id" =~ $FEEDBACK_UUID4_PATTERN ]]; then
        echo -e "${GREEN}✓ PASS${NC}: feedback_id matches pattern"
        ((TESTS_PASSED++))
    else
        echo -e "${RED}✗ FAIL${NC}: feedback_id should match fb-<uuid4> pattern"
        echo -e "  Actual ID: '$id'"
        ((TESTS_FAILED++))
        exit 1
    fi
done

step "Assert all feedback IDs are unique"
UNIQUE_COUNT=$(printf "%s\n" "${IDS[@]}" | sort -u | wc -l | tr -d ' ')
assert_equals "5" "$UNIQUE_COUNT" "all feedback IDs should be unique"

test_end
