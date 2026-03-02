#!/bin/bash
# test-read-03.sh — READ-03: Malformed feedback ID
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "READ-03" "Malformed feedback ID"

step "Lookup feedback with invalid ID"
http_get "/feedback/not-a-valid-id"

step "Assert 404 and no stack traces"
assert_status "404"
assert_body_not_contains "Traceback" "stacktrace" "stack_trace" "Internal Server Error"

step "Lookup feedback with path traversal attempt"
http_get "/feedback/..%2F..%2Fetc%2Fpasswd"

step "Assert 404 and no internal info leaked"
assert_status "404"
assert_body_not_contains "Traceback" "stacktrace" "stack_trace" "Internal Server Error"

test_end
