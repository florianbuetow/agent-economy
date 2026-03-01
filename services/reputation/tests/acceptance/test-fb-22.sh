#!/bin/bash
# test-fb-22.sh — FB-22: Oversized request body
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "FB-22" "Oversized request body"

step "Create a ~2MB JSON body"
TMP_FILE=$(mktemp)
printf '{"task_id":"t-test","from_agent_id":"a-test","to_agent_id":"a-test2","category":"delivery_quality","rating":"satisfied","comment":"' > "$TMP_FILE"
dd if=/dev/zero bs=1024 count=2048 2>/dev/null | tr '\0' 'A' >> "$TMP_FILE"
printf '"}' >> "$TMP_FILE"

step "Submit oversized body"
http_post_file "/feedback" "$TMP_FILE"
rm -f "$TMP_FILE"

step "Assert 413 with PAYLOAD_TOO_LARGE"
assert_status "413"
assert_json_eq ".error" "PAYLOAD_TOO_LARGE"

test_end
