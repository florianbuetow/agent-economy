#!/bin/bash
# test-reg-17.sh — REG-17: Wrong content type
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-17" "Wrong content type"

step "Generate a valid keypair"
crypto_keygen

step "Submit JSON body with text/plain content type"
BODY=$(jq -nc --arg name "Alice" --arg public_key "$PUBLIC_KEY" '{name:$name, public_key:$public_key}')
http_post_content_type "/agents/register" "text/plain" "$BODY"

step "Assert unsupported media type error"
assert_status "415"
assert_json_eq ".error" "UNSUPPORTED_MEDIA_TYPE"

test_end
