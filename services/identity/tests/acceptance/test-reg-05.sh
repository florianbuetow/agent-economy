#!/bin/bash
# test-reg-05.sh — REG-05: Duplicate names are allowed
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-05" "Duplicate names are allowed"

step "Register first agent with shared name"
crypto_keygen
register_agent "SharedName" "$PUBLIC_KEY"
ID1="$AGENT_ID"

step "Register second agent with same name and different key"
crypto_keygen
register_agent "SharedName" "$PUBLIC_KEY"
ID2="$AGENT_ID"

step "Assert both IDs are unique"
assert_not_equals "$ID1" "$ID2" "duplicate names should still produce unique IDs"

test_end
