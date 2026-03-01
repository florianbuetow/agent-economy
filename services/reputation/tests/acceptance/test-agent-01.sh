#!/bin/bash
# test-agent-01.sh — AGENT-01: No feedback about agent
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "AGENT-01" "No feedback about agent"

step "Generate random agent ID"
AGENT_ID="$(gen_agent_id)"

step "Query feedback about agent with no feedback"
http_get "/feedback/agent/$AGENT_ID"

step "Assert 200, agent_id matches, feedback is empty array"
assert_status "200"
assert_json_eq ".agent_id" "$AGENT_ID"
assert_json_array_length ".feedback" 0

test_end
