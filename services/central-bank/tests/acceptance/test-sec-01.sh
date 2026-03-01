#!/bin/bash
# test-sec-01.sh — SEC-01: Error envelope consistency
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "SEC-01" "Error envelope consistency"

step "INVALID_JWS on account creation (missing token)"
http_post "/accounts" '{}'
assert_status "400"
assert_json_eq ".error" "INVALID_JWS"
assert_error_envelope

step "INVALID_JSON on account creation"
http_post_raw "/accounts" '{broken'
assert_status "400"
assert_json_eq ".error" "INVALID_JSON"
assert_error_envelope

step "INVALID_JWS on balance read (missing Authorization header)"
http_get "/accounts/a-00000000-0000-0000-0000-000000000000"
assert_status "400"
assert_json_eq ".error" "INVALID_JWS"
assert_error_envelope

step "INVALID_JWS on escrow lock (missing token)"
http_post "/escrow/lock" '{}'
assert_status "400"
assert_json_eq ".error" "INVALID_JWS"
assert_error_envelope

step "ESCROW_NOT_FOUND on release"
PAYLOAD=$(jq -nc --arg escrow_id "esc-fake" --arg recipient "a-00000000-0000-0000-0000-000000000000" '{action:"escrow_release", escrow_id:$escrow_id, recipient_account_id:$recipient}')
jws_sign "$PLATFORM_PRIVATE_KEY_HEX" "$PLATFORM_AGENT_ID" "$PAYLOAD"
BODY=$(jq -nc --arg token "$JWS_TOKEN" '{token:$token}')
http_post "/escrow/esc-fake/release" "$BODY"
assert_status "404"
assert_json_eq ".error" "ESCROW_NOT_FOUND"
assert_error_envelope

step "UNSUPPORTED_MEDIA_TYPE on account creation"
http_post_content_type "/accounts" "text/plain" '{}'
assert_status "415"
assert_json_eq ".error" "UNSUPPORTED_MEDIA_TYPE"
assert_error_envelope

test_end

