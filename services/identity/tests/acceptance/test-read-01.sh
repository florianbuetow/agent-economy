#!/bin/bash
# test-read-01.sh — READ-01: Lookup existing agent
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "READ-01" "Lookup existing agent"

step "Register Alice"
crypto_keygen
PUB_A="$PUBLIC_KEY"
register_agent "Alice" "$PUB_A"
ALICE_ID="$AGENT_ID"

step "Read Alice by agent_id"
http_get "/agents/$ALICE_ID"

step "Assert full record matches registration"
assert_status "200"
assert_json_eq ".agent_id" "$ALICE_ID"
assert_json_eq ".name" "Alice"
assert_json_eq ".public_key" "$PUB_A"
assert_json_exists ".registered_at"

test_end
