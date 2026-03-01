#!/bin/bash
# test-reg-15.sh — REG-15: Mass-assignment resistance
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-15" "Mass-assignment resistance"

step "Generate a valid keypair"
crypto_keygen

step "Submit registration with extra privileged/system fields"
BODY=$(jq -nc --arg name "Alice" --arg public_key "$PUBLIC_KEY" '{name:$name, public_key:$public_key, agent_id:"a-00000000-0000-0000-0000-000000000000", registered_at:"1999-01-01T00:00:00Z", is_admin:true}')
http_post "/agents/register" "$BODY"

step "Assert service ignores mass-assigned fields"
assert_status "201"
assert_json_matches ".agent_id" "$UUID4_PATTERN"
assert_json_not_equals_expected="a-00000000-0000-0000-0000-000000000000"
ACTUAL_ID=$(echo "$HTTP_BODY" | jq -r '.agent_id')
assert_not_equals "$assert_json_not_equals_expected" "$ACTUAL_ID" "service must generate agent_id"
ACTUAL_REGISTERED_AT=$(echo "$HTTP_BODY" | jq -r '.registered_at')
assert_not_equals "1999-01-01T00:00:00Z" "$ACTUAL_REGISTERED_AT" "service must generate registered_at"

test_end
