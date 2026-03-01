#!/bin/bash
# test-http-01.sh — HTTP-01: Wrong method on defined routes is blocked
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HTTP-01" "Wrong method on defined routes is blocked"

DUMMY_ID="fb-00000000-0000-0000-0000-000000000000"
DUMMY_TASK="t-00000000-0000-0000-0000-000000000000"
DUMMY_AGENT="a-00000000-0000-0000-0000-000000000000"

check_method() {
    local method="$1"
    local path="$2"
    http_method "$method" "$path"
    assert_status "405"
    assert_json_eq ".error" "METHOD_NOT_ALLOWED"
}

step "Check method misuse on POST-only /feedback route"
check_method "GET" "/feedback"
check_method "PUT" "/feedback"
check_method "DELETE" "/feedback"

step "Check method misuse on GET-only /feedback/{feedback_id} route"
check_method "POST" "/feedback/$DUMMY_ID"
check_method "PUT" "/feedback/$DUMMY_ID"
check_method "DELETE" "/feedback/$DUMMY_ID"

step "Check method misuse on GET-only /feedback/task/{task_id} route"
check_method "POST" "/feedback/task/$DUMMY_TASK"
check_method "PUT" "/feedback/task/$DUMMY_TASK"
check_method "DELETE" "/feedback/task/$DUMMY_TASK"

step "Check method misuse on GET-only /feedback/agent/{agent_id} route"
check_method "POST" "/feedback/agent/$DUMMY_AGENT"
check_method "PUT" "/feedback/agent/$DUMMY_AGENT"
check_method "DELETE" "/feedback/agent/$DUMMY_AGENT"

step "Check method misuse on GET-only /health route"
check_method "POST" "/health"

test_end
