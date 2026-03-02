#!/bin/bash
# test-sec-01.sh — SEC-01: Error envelope consistency
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "SEC-01" "Error envelope consistency"

step "MISSING_FIELD on registration"
http_post "/agents/register" '{}'
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"
assert_error_envelope

step "INVALID_PUBLIC_KEY on registration"
http_post "/agents/register" '{"name":"X","public_key":"bad"}'
assert_status "400"
assert_json_eq ".error" "INVALID_PUBLIC_KEY"
assert_error_envelope

step "INVALID_JSON on registration"
http_post_raw "/agents/register" '{broken'
assert_status "400"
assert_json_eq ".error" "INVALID_JSON"
assert_error_envelope

step "AGENT_NOT_FOUND on read"
http_get "/agents/a-00000000-0000-0000-0000-000000000000"
assert_status "404"
assert_json_eq ".error" "AGENT_NOT_FOUND"
assert_error_envelope

step "METHOD_NOT_ALLOWED on route misuse"
http_method "DELETE" "/agents/register"
assert_status "405"
assert_json_eq ".error" "METHOD_NOT_ALLOWED"
assert_error_envelope

step "UNSUPPORTED_MEDIA_TYPE on registration"
http_post_content_type "/agents/register" "text/plain" '{}'
assert_status "415"
assert_json_eq ".error" "UNSUPPORTED_MEDIA_TYPE"
assert_error_envelope

step "MISSING_FIELD on verification"
http_post "/agents/verify" '{}'
assert_status "400"
assert_json_eq ".error" "MISSING_FIELD"
assert_error_envelope

step "INVALID_BASE64 on verification"
crypto_keygen
register_agent "Base64Case" "$PUBLIC_KEY"
BAD_BODY=$(jq -nc --arg agent_id "$AGENT_ID" --arg payload "%%%" --arg signature "aaa" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$BAD_BODY"
assert_status "400"
assert_json_eq ".error" "INVALID_BASE64"
assert_error_envelope

step "INVALID_FIELD_TYPE on registration"
INVALID_TYPE_BODY=$(jq -nc '{name:123, public_key:true}')
http_post "/agents/register" "$INVALID_TYPE_BODY"
assert_status "400"
assert_json_eq ".error" "INVALID_FIELD_TYPE"
assert_error_envelope

step "INVALID_NAME on registration"
crypto_keygen
INVALID_NAME_BODY=$(jq -nc --arg name "" --arg public_key "$PUBLIC_KEY" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$INVALID_NAME_BODY"
assert_status "400"
assert_json_eq ".error" "INVALID_NAME"
assert_error_envelope

step "PUBLIC_KEY_EXISTS on duplicate registration"
crypto_keygen
register_agent "DupKeyOriginal" "$PUBLIC_KEY"
DUP_KEY_BODY=$(jq -nc --arg name "DupKeyDuplicate" --arg public_key "$PUBLIC_KEY" '{name:$name, public_key:$public_key}')
http_post "/agents/register" "$DUP_KEY_BODY"
assert_status "409"
assert_json_eq ".error" "PUBLIC_KEY_EXISTS"
assert_error_envelope

step "INVALID_SIGNATURE_LENGTH on verification"
crypto_keygen
register_agent "ShortSigCase" "$PUBLIC_KEY"
SHORT_SIG=$(crypto_random_b64 32)
SHORT_SIG_BODY=$(jq -nc --arg agent_id "$AGENT_ID" --arg payload "aGVsbG8=" --arg signature "$SHORT_SIG" '{agent_id:$agent_id, payload:$payload, signature:$signature}')
http_post "/agents/verify" "$SHORT_SIG_BODY"
assert_status "400"
assert_json_eq ".error" "INVALID_SIGNATURE_LENGTH"
assert_error_envelope

step "PAYLOAD_TOO_LARGE on registration"
TMP_FILE=$(mktemp)
printf '{"name":"' > "$TMP_FILE"
dd if=/dev/zero bs=1024 count=2048 2>/dev/null | tr '\0' 'A' >> "$TMP_FILE"
printf '","public_key":"ed25519:AAAA"}' >> "$TMP_FILE"
http_post_file "/agents/register" "$TMP_FILE"
rm -f "$TMP_FILE"
assert_status "413"
assert_json_eq ".error" "PAYLOAD_TOO_LARGE"
assert_error_envelope

test_end
