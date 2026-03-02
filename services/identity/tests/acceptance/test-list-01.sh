#!/bin/bash
# test-list-01.sh — LIST-01: Empty list on fresh system
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "LIST-01" "Empty list on fresh system"

step "List agents"
http_get "/agents"

step "Assert list is empty"
assert_status "200"
AGENT_COUNT=$(echo "$HTTP_BODY" | jq '.agents | length')
assert_equals "0" "$AGENT_COUNT" "fresh system should have zero agents"

test_end
