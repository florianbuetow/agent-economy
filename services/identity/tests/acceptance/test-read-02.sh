#!/bin/bash
# test-read-02.sh — READ-02: Lookup non-existent agent
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "READ-02" "Lookup non-existent agent"

step "Read missing agent"
http_get "/agents/a-00000000-0000-0000-0000-000000000000"

step "Assert agent not found"
assert_status "404"
assert_json_eq ".error" "AGENT_NOT_FOUND"

test_end
