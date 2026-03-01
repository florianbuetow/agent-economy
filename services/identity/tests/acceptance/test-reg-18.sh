#!/bin/bash
# test-reg-18.sh — REG-18: Oversized body
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "REG-18" "Oversized body"

step "Create an oversized JSON payload (~2MB)"
TMP_FILE=$(mktemp)
printf '{"name":"' > "$TMP_FILE"
dd if=/dev/zero bs=1024 count=2048 2>/dev/null | tr '\0' 'A' >> "$TMP_FILE"
printf '","public_key":"ed25519:AAAA"}' >> "$TMP_FILE"

step "Submit oversized payload"
http_post_file "/agents/register" "$TMP_FILE"
rm -f "$TMP_FILE"

step "Assert payload too large error"
assert_status "413"
assert_json_eq ".error" "PAYLOAD_TOO_LARGE"

test_end
