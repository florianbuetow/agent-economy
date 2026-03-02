#!/bin/bash
# test-read-03.sh — READ-03: Malformed/path-traversal ID
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "READ-03" "Malformed/path-traversal ID"

step "Request malformed non-UUID agent ID"
http_get "/agents/not-a-valid-id"
assert_status "404"
assert_body_not_contains "Traceback" "File \"" "sqlite" "/home/" "/Users/"

step "Request path-traversal-encoded ID"
http_get "/agents/..%2F..%2Fetc%2Fpasswd"
assert_status "404"
assert_body_not_contains "Traceback" "File \"" "sqlite" "/home/" "/Users/" "root:"

test_end
