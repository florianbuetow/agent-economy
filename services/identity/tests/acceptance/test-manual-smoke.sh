#!/bin/bash
# test-manual-smoke.sh — MANUAL-SMOKE: Comprehensive smoke test
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "MANUAL-SMOKE" "Comprehensive smoke test"

step "Health endpoint returns 200 with expected schema"
http_get "/health"
assert_status "200"
assert_json_exists ".status"
assert_json_exists ".uptime_seconds"
assert_json_exists ".started_at"
assert_json_exists ".registered_agents"
assert_json_field ".status" "ok"
assert_json_field ".registered_agents" "0"

step "Register one agent with a valid Ed25519 key"
crypto_keygen
PRIV_A="$PRIVATE_KEY_HEX"
PUB_A="$PUBLIC_KEY"
BODY=$(jq -nc --arg name "Alice" --arg public_key "$PUB_A" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY"
assert_status "201"
assert_json_exists ".agent_id"
assert_json_exists ".name"
assert_json_exists ".public_key"
assert_json_exists ".registered_at"
assert_json_field ".name" "Alice"
assert_json_field ".public_key" "$PUB_A"
assert_json_matches ".agent_id" "$UUID4_PATTERN"
assert_json_matches ".registered_at" "$ISO8601_PATTERN"
ALICE_ID=$(echo "$HTTP_BODY" | jq -r '.agent_id')

step "List agents returns one summary item without public_key"
http_get "/agents"
assert_status "200"
assert_json_exists ".agents"
AGENT_COUNT=$(echo "$HTTP_BODY" | jq '.agents | length')
assert_equals "1" "$AGENT_COUNT" "list should contain exactly one agent"
assert_json_field ".agents[0].agent_id" "$ALICE_ID"
assert_json_field ".agents[0].name" "Alice"
assert_json_exists ".agents[0].registered_at"
assert_json_not_exists ".agents[0].public_key"

step "Get agent by ID returns full record including public_key"
http_get "/agents/$ALICE_ID"
assert_status "200"
assert_json_field ".agent_id" "$ALICE_ID"
assert_json_field ".name" "Alice"
assert_json_field ".public_key" "$PUB_A"
assert_json_exists ".registered_at"

step "Get nonexistent agent returns 404 AGENT_NOT_FOUND"
http_get "/agents/a-00000000-0000-0000-0000-000000000000"
assert_status "404"
assert_json_field ".error" "AGENT_NOT_FOUND"
assert_json_exists ".message"
assert_json_exists ".details"

step "Verify a valid signature returns valid=true"
crypto_sign_raw "$PRIV_A" "manual smoke payload"
BODY=$(jq -nc --arg agent_id "$ALICE_ID" --arg payload "$PAYLOAD_B64" --arg signature "$SIGNATURE_B64" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"
assert_status "200"
assert_json_true ".valid"
assert_json_field ".agent_id" "$ALICE_ID"

step "Verify a wrong signature returns valid=false with reason"
crypto_keygen
PRIV_B="$PRIVATE_KEY_HEX"
crypto_sign_raw "$PRIV_B" "manual smoke payload"
BODY=$(jq -nc --arg agent_id "$ALICE_ID" --arg payload "$PAYLOAD_B64" --arg signature "$SIGNATURE_B64" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BODY"
assert_status "200"
assert_json_false ".valid"
assert_json_field ".reason" "signature mismatch"

step "Duplicate public key is rejected"
BODY=$(jq -nc --arg name "Eve" --arg public_key "$PUB_A" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$BODY"
assert_status "409"
assert_json_field ".error" "PUBLIC_KEY_EXISTS"
assert_json_exists ".message"
assert_json_exists ".details"

step "Missing field returns 400 MISSING_FIELD"
BODY=$(jq -nc --arg public_key "$PUB_A" '{public_key:$public_key}')
http_post "/agents/register" "$BODY"
assert_status "400"
assert_json_field ".error" "MISSING_FIELD"
assert_json_field ".details.field" "name"

step "Invalid JSON returns 400 INVALID_JSON"
http_post_raw "/agents/register" '{"name":"Alice","public_key":"ed2'
assert_status "400"
assert_json_field ".error" "INVALID_JSON"
assert_json_exists ".message"
assert_json_exists ".details"

step "Wrong content type returns 415 UNSUPPORTED_MEDIA_TYPE"
crypto_keygen
BODY=$(jq -nc --arg name "Mallory" --arg public_key "$PUBLIC_KEY" '{name:$name, public_key:$public_key}')
http_post_content_type "/agents/register" "text/plain" "$BODY"
assert_status "415"
assert_json_field ".error" "UNSUPPORTED_MEDIA_TYPE"
assert_json_exists ".message"
assert_json_exists ".details"

step "GET /agents/register returns 405 METHOD_NOT_ALLOWED"
http_method "GET" "/agents/register"
assert_status "405"
assert_json_field ".error" "METHOD_NOT_ALLOWED"
assert_json_exists ".message"
assert_json_exists ".details"

step "POST /health returns 405 METHOD_NOT_ALLOWED"
http_method "POST" "/health"
assert_status "405"
assert_json_field ".error" "METHOD_NOT_ALLOWED"
assert_json_exists ".message"
assert_json_exists ".details"

step "Health reports exactly one registered agent"
http_get "/health"
assert_status "200"
assert_json_field ".status" "ok"
assert_json_field ".registered_agents" "1"
assert_json_exists ".uptime_seconds"
assert_json_exists ".started_at"

test_end
