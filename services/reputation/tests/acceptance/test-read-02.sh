#!/bin/bash
# test-read-02.sh — READ-02: Lookup non-existent feedback
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "READ-02" "Lookup non-existent feedback"

step "Lookup feedback with non-existent ID"
http_get "/feedback/fb-00000000-0000-0000-0000-000000000000"

step "Assert 404 with FEEDBACK_NOT_FOUND error"
assert_status "404"
assert_json_eq ".error" "FEEDBACK_NOT_FOUND"

test_end
