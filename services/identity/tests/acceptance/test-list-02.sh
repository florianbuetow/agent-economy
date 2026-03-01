#!/bin/bash
# test-list-02.sh — LIST-02: Populated list omits public keys
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "LIST-02" "Populated list omits public keys"

step "Register two agents"
crypto_keygen
register_agent "Alice" "$PUBLIC_KEY"
crypto_keygen
register_agent "Bob" "$PUBLIC_KEY"

step "List agents"
http_get "/agents"

step "Assert list shape and public key omission"
assert_status "200"
assert_json_array_min_length ".agents" "2"
HAS_PUBLIC_KEY=$(echo "$HTTP_BODY" | jq -r '[.agents[] | has("public_key")] | any')
assert_equals "false" "$HAS_PUBLIC_KEY" "list entries should omit public_key"
HAS_REQUIRED_FIELDS=$(echo "$HTTP_BODY" | jq -r '[.agents[] | (has("agent_id") and has("name") and has("registered_at"))] | all')
assert_equals "true" "$HAS_REQUIRED_FIELDS" "all list entries should include agent_id, name, registered_at"

test_end
