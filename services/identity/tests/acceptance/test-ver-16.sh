#!/bin/bash
# test-ver-16.sh — VER-16: Wrong content type
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "VER-16" "Wrong content type"

step "Submit verify request with text/plain content type"
http_post_content_type "/agents/verify" "text/plain" '{"agent_id":"x","payload":"y","signature":"z"}'

step "Assert unsupported media type error"
assert_status "415"
assert_json_eq ".error" "UNSUPPORTED_MEDIA_TYPE"

test_end
