#!/bin/bash
# test-fb-18.sh — FB-18: Wrong content type
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-18" "Wrong content type"

step "Send request with text/plain content type"
http_post_content_type "/feedback" "text/plain" "not json"

step "Assert 415 with UNSUPPORTED_MEDIA_TYPE error"
assert_status "415"
assert_json_eq ".error" "UNSUPPORTED_MEDIA_TYPE"

test_end
