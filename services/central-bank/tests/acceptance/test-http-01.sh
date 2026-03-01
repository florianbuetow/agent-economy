#!/bin/bash
# test-http-01.sh — HTTP-01: Wrong HTTP methods return 405
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HTTP-01" "Wrong HTTP methods return 405"

check_method() {
    local method="$1"
    local path="$2"
    http_method "$method" "$path"
    assert_status "405"
    assert_json_eq ".error" "METHOD_NOT_ALLOWED"
}

step "Check method misuse combinations"
check_method "GET" "/accounts"
check_method "DELETE" "/accounts"
check_method "GET" "/escrow/lock"
check_method "DELETE" "/escrow/lock"
check_method "POST" "/health"

test_end

