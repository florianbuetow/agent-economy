#!/bin/bash
# test-reg-02.sh — REG-02: Register second valid agent with different key
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-02" "Register second valid agent with different key"

step "Register Alice with first keypair"
crypto_keygen
register_agent "Alice" "$PUBLIC_KEY"
ALICE_ID="$AGENT_ID"

step "Register Bob with second keypair"
crypto_keygen
register_agent "Bob" "$PUBLIC_KEY"
BOB_ID="$AGENT_ID"

step "Assert IDs are different"
assert_not_equals "$ALICE_ID" "$BOB_ID" "agent IDs should differ"

test_end
