#!/bin/bash
# test-fb-20.sh — FB-20: Concurrent duplicate feedback race is safe
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-20" "Concurrent duplicate feedback race is safe"

step "Generate two agent IDs and a task ID"
ALICE=$(gen_agent_id)
BOB=$(gen_agent_id)
TASK=$(gen_task_id)

step "Send two identical feedback POST requests in parallel"
BODY=$(jq -nc \
    --arg task_id "$TASK" \
    --arg from_agent_id "$ALICE" \
    --arg to_agent_id "$BOB" \
    --arg category "delivery_quality" \
    --arg rating "satisfied" \
    --arg comment "Race test" \
    '{task_id:$task_id, from_agent_id:$from_agent_id, to_agent_id:$to_agent_id, category:$category, rating:$rating, comment:$comment}')

TMP1=$(mktemp)
TMP2=$(mktemp)

curl -s -o /dev/null -w '%{http_code}' -X POST -H "Content-Type: application/json" "${BASE_URL}/feedback" -d "$BODY" > "$TMP1" &
PID1=$!

curl -s -o /dev/null -w '%{http_code}' -X POST -H "Content-Type: application/json" "${BASE_URL}/feedback" -d "$BODY" > "$TMP2" &
PID2=$!

wait $PID1
wait $PID2

STATUS1=$(cat "$TMP1")
STATUS2=$(cat "$TMP2")
rm -f "$TMP1" "$TMP2"

step "Assert exactly one 201 and one 409"
SORTED=$(echo -e "$STATUS1\n$STATUS2" | sort)
FIRST=$(echo "$SORTED" | head -1)
SECOND=$(echo "$SORTED" | tail -1)

assert_equals "201" "$FIRST" "one request returned 201"
assert_equals "409" "$SECOND" "other request returned 409"

test_end
