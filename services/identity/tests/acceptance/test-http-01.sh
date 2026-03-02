#!/bin/bash
# test-http-01.sh — HTTP-01: Wrong method on defined routes
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
source "$SCRIPT_DIR/helpers.sh"

test_start "HTTP-01" "Wrong method on defined routes"

check_method() {
    local method="$1"
    local path="$2"
    http_method "$method" "$path"
    assert_status "405"
    assert_json_eq ".error" "METHOD_NOT_ALLOWED"
}

step "Check method misuse combinations"
check_method "GET" "/agents/register"
check_method "PUT" "/agents/register"
check_method "GET" "/agents/verify"
check_method "POST" "/agents/a-00000000-0000-0000-0000-000000000000"
check_method "PATCH" "/agents/a-00000000-0000-0000-0000-000000000000"
check_method "DELETE" "/agents/a-00000000-0000-0000-0000-000000000000"
check_method "POST" "/agents"
check_method "POST" "/health"

test_end
