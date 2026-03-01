#!/bin/bash
# test-ver-18.sh — VER-18: SQL injection in agent_id
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-18" "SQL injection in agent_id"

step "Generate random signature"
FAKE_SIG=$(crypto_random_b64 64)

step "Verify using SQL injection string as agent_id"
BODY=$(jq -nc --arg agent_id "' OR '1'='1" --arg payload "aGVsbG8=" --arg signature "$FAKE_SIG" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"

step "Assert agent not found"
assert_status "404"
assert_json_eq ".error" "AGENT_NOT_FOUND"

test_end
